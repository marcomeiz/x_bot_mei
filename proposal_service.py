import os
import threading
from typing import Dict, Optional
from urllib.parse import quote

from callback_parser import CallbackAction, CallbackType, parse_callback
from core_generator import (
    find_relevant_topic,
    generate_third_tweet_variant,
    generate_tweet_from_topic,
    find_topic_by_id,
)
from variant_generators import VariantCResult
from draft_repository import DraftPayload, DraftRepository
from embeddings_manager import get_embedding, get_memory_collection
from evaluation import evaluate_draft
from logger_config import logger
from prompt_context import build_prompt_context
from style_guard import StyleRejection
from telegram_client import TelegramClient


class ProposalService:
    def __init__(
        self,
        telegram: TelegramClient,
        draft_repo: Optional[DraftRepository] = None,
        similarity_threshold: float = 0.20,
    ) -> None:
        self.telegram = telegram
        self.drafts = draft_repo or DraftRepository("/tmp")
        self.similarity_threshold = similarity_threshold
        self.share_base_url = os.getenv(
            "THREADS_SHARE_URL",
            "https://www.threads.net/intent/post?text=",
        )
        self.show_internal_summary = os.getenv("SHOW_INTERNAL_SUMMARY", "0").lower() in {"1", "true", "yes", "y"}

    # ------------------------------------------------------------------ public
    def do_the_work(self, chat_id: int) -> None:
        logger.info("[CHAT_ID: %s] Iniciando nuevo ciclo de generación.", chat_id)
        max_retries = 5
        for attempt in range(1, max_retries + 1):
            logger.info("[CHAT_ID: %s] Intento %s/%s de encontrar tema.", chat_id, attempt, max_retries)
            topic = find_relevant_topic()
            if not topic:
                continue
            if self.propose_tweet(chat_id, topic):
                logger.info("[CHAT_ID: %s] Propuesta enviada correctamente.", chat_id)
                return
            logger.warning("[CHAT_ID: %s] Tema descartado por similitud. Reintentando.", chat_id)
            self.telegram.send_message(chat_id, "⚠️ Tema similar a uno previo. Buscando otro…")

        logger.warning("[CHAT_ID: %s] Sin tema único tras %s intentos. Permitiremos similitud.", chat_id, max_retries)
        topic = find_relevant_topic()
        if topic and self.propose_tweet(chat_id, topic, ignore_similarity=True):
            logger.info("[CHAT_ID: %s] Propuesta enviada con similitud permitida.", chat_id)
            return
        self.telegram.send_message(
            chat_id,
            "❌ No pude generar propuesta, incluso permitiendo similitud.",
            reply_markup=self.telegram.get_new_tweet_keyboard(),
        )

    def propose_tweet(self, chat_id: int, topic: Dict, ignore_similarity: bool = False) -> bool:
        topic_abstract = topic.get("abstract")
        topic_id = topic.get("topic_id")
        source_pdf = topic.get("source_pdf")
        logger.info(
            "[CHAT_ID: %s] Tema seleccionado (ID: %s). Abstract: '%s...'",
            chat_id,
            topic_id,
            (topic_abstract or "")[:80],
        )

        pre_lines = [
            "🧠 Seleccionando tema…",
            f"✍️ Tema: {self.telegram.clean_abstract(topic_abstract)[:80]}…",
        ]
        if source_pdf:
            pre_lines.append(f"📄 Origen: {source_pdf}")
        pre_lines.append("Generando 3 alternativas…")
        self.telegram.send_message(chat_id, "\n".join(pre_lines))

        ab_result = generate_tweet_from_topic(topic_abstract, ignore_similarity=ignore_similarity)
        draft_a = ab_result.draft_a
        draft_b = ab_result.draft_b
        try:
            c_result = generate_third_tweet_variant(topic_abstract)
            draft_c = c_result.draft
            category_name = c_result.category
        except StyleRejection as rejection:
            feedback = str(rejection).strip()
            logger.warning("[CHAT_ID: %s] Variante C rechazada: %s", chat_id, feedback)
            feedback_short = (feedback[:200] + "…") if len(feedback) > 200 else feedback
            draft_c = f"[Rejected by final reviewer: {feedback_short}]"
            category_name = "Rejected"
            c_result = VariantCResult(draft=draft_c, category=category_name, reasoning_summary=None)

        context = build_prompt_context()
        evaluations: Dict[str, Dict[str, object]] = {}
        evaluation_a = evaluate_draft(draft_a, context)
        if evaluation_a:
            evaluations["A"] = evaluation_a
        evaluation_b = evaluate_draft(draft_b, context)
        if evaluation_b:
            evaluations["B"] = evaluation_b
        if draft_c and category_name != "Rejected":
            evaluation_c = evaluate_draft(draft_c, context)
            if evaluation_c:
                evaluations["C"] = evaluation_c

        if draft_a.startswith("Error: El tema es demasiado similar"):
            return False
        if draft_a.startswith("Error:"):
            logger.error("[CHAT_ID: %s] Error desde generador: %s", chat_id, draft_a)
            self.telegram.send_message(
                chat_id,
                f"Hubo un problema: {self.telegram.escape(draft_a)}",
                reply_markup=self.telegram.get_new_tweet_keyboard(),
                as_html=True,
            )
            return False

        payload = DraftPayload(
            draft_a=draft_a,
            draft_b=draft_b,
            draft_c=draft_c,
            category=category_name,
        )
        self.drafts.save(chat_id, topic_id, payload)

        keyboard = self.telegram.build_proposal_keyboard(
            topic_id,
            has_variant_c=bool(draft_c),
            allow_variant_c=bool(draft_c) and category_name != "Rejected",
        )

        message_text = self.telegram.format_proposal_message(
            topic_id,
            topic_abstract or "",
            source_pdf,
            draft_a,
            draft_b,
            draft_c,
            category_name,
            evaluations=evaluations,
        )

        if self.telegram.send_message(chat_id, message_text, reply_markup=keyboard, as_html=True):
            if self.show_internal_summary:
                summary_blocks = []
                if ab_result.reasoning_summary:
                    summary_blocks.append(ab_result.reasoning_summary)
                if c_result.reasoning_summary:
                    c_summary = c_result.reasoning_summary
                    if c_summary.startswith("🧠"):
                        c_summary = c_summary.replace("🧠", "🧠 (C)", 1)
                    summary_blocks.append(c_summary)
                if summary_blocks:
                    self.telegram.send_message(chat_id, "\n\n".join(summary_blocks))
            return True
        logger.error("[CHAT_ID: %s] Falló el envío de propuestas para topic %s.", chat_id, topic_id)
        return False

    def handle_callback_query(self, update: Dict) -> None:
        query = update.get("callback_query", {})
        chat_id = query["message"]["chat"]["id"]
        message_id = query["message"]["message_id"]
        callback_data = query.get("data", "")
        logger.info("[CHAT_ID: %s] Callback recibido: '%s'", chat_id, callback_data)

        action = parse_callback(callback_data)
        original_message_text = query["message"].get("text", "")

        if action.type == CallbackType.APPROVE:
            self._handle_approve(chat_id, message_id, action, original_message_text)
        elif action.type == CallbackType.CONFIRM:
            self._handle_confirm(chat_id, action)
        elif action.type == CallbackType.CANCEL:
            logger.info("[CHAT_ID: %s] Confirmación cancelada.", chat_id)
            self.telegram.send_message(chat_id, "Operación cancelada.", reply_markup=self.telegram.get_new_tweet_keyboard())
            if action.topic_id:
                self.drafts.delete(chat_id, action.topic_id)
        elif action.type == CallbackType.REJECT:
            logger.info("[CHAT_ID: %s] Ambas opciones rechazadas para topic %s.", chat_id, action.topic_id)
            appended = "❌ <b>Rechazados.</b>"
            new_text = (original_message_text or "") + "\n\n" + appended
            self.telegram.edit_message(
                chat_id,
                message_id,
                new_text,
                reply_markup=self.telegram.get_new_tweet_keyboard(),
                as_html=True,
            )
            if action.topic_id:
                self.drafts.delete(chat_id, action.topic_id)
        elif action.type == CallbackType.COPY:
            self._handle_copy(chat_id, action)
        elif action.type == CallbackType.NOOP:
            logger.info("[CHAT_ID: %s] Acción noop ignorada.", chat_id)
        elif action.type == CallbackType.GENERATE:
            logger.info("[CHAT_ID: %s] Solicitud de nueva propuesta manual.", chat_id)
            self.telegram.edit_message(chat_id, message_id, "🚀 Buscando un nuevo tema…")
            threading.Thread(target=self.do_the_work, args=(chat_id,)).start()
        else:
            logger.warning("[CHAT_ID: %s] Callback no reconocido: %s", chat_id, callback_data)

    # -------------------------------------------------------------- helpers
    def _handle_approve(
        self,
        chat_id: int,
        message_id: int,
        action: CallbackAction,
        original_text: str,
    ) -> None:
        if not action.topic_id or not action.option:
            logger.warning("[CHAT_ID: %s] Callback approve incompleto: %s", chat_id, action.raw)
            self.telegram.send_message(chat_id, "⚠️ No pude localizar el borrador aprobado.")
            return

        appended = f"✅ <b>{self.telegram.escape(f'¡Aprobada Opción {action.option}!')}</b>"
        new_text = (original_text or "") + "\n\n" + appended
        self.telegram.edit_message(chat_id, message_id, new_text, as_html=True)

        try:
            payload = self.drafts.load(chat_id, action.topic_id)
        except FileNotFoundError:
            logger.error("[CHAT_ID: %s] No se encontró draft para %s.", chat_id, action.topic_id)
            self.telegram.send_message(
                chat_id,
                "⚠️ No pude recuperar el borrador aprobado (quizá expiró). Genera uno nuevo con el botón.",
                reply_markup=self.telegram.get_new_tweet_keyboard(),
            )
            return

        chosen_tweet = payload.get_variant(action.option)
        if not chosen_tweet:
            logger.error("[CHAT_ID: %s] Opción %s no encontrada en draft %s.", chat_id, action.option, action.topic_id)
            self.telegram.send_message(chat_id, "⚠️ La opción elegida no está disponible.")
            return

        memory_collection = get_memory_collection()
        tweet_embedding = get_embedding(chosen_tweet)
        if tweet_embedding and memory_collection.count() > 0:
            try:
                query = memory_collection.query(query_embeddings=[tweet_embedding], n_results=1)
                dist = query and query.get("distances") and query["distances"][0][0]
                distance_value = float(dist) if isinstance(dist, (int, float)) else 1.0
            except Exception:
                distance_value = 1.0
            if distance_value < self.similarity_threshold:
                logger.warning(
                    "[CHAT_ID: %s] Borrador muy similar (dist=%s < %s).",
                    chat_id,
                    distance_value,
                    self.similarity_threshold,
                )
                warn = (
                    "⚠️ El borrador elegido parece muy similar a una publicación previa.\n"
                    f"Distancia: {distance_value:.4f} (umbral {self.similarity_threshold}).\n"
                    "¿Confirmas guardarlo igualmente?"
                )
                keyboard = {
                    "inline_keyboard": [
                        [
                            {"text": "✅ Confirmar", "callback_data": f"confirm_{action.option}_{action.topic_id}"},
                            {"text": "❌ Cancelar", "callback_data": f"cancel_{action.topic_id}"},
                        ]
                    ]
                }
                self.telegram.send_message(chat_id, warn, reply_markup=keyboard)
                return

        self._finalize_choice(chat_id, action.option, action.topic_id, chosen_tweet)

    def _handle_confirm(self, chat_id: int, action: CallbackAction) -> None:
        if not action.topic_id or not action.option:
            logger.warning("[CHAT_ID: %s] Callback confirm incompleto: %s", chat_id, action.raw)
            self.telegram.send_message(chat_id, "⚠️ No pude completar la confirmación. Genera uno nuevo.")
            return
        try:
            payload = self.drafts.load(chat_id, action.topic_id)
            chosen_tweet = payload.get_variant(action.option)
            if not chosen_tweet:
                raise ValueError("Opción elegida no encontrada")
            self._finalize_choice(
                chat_id,
                action.option,
                action.topic_id,
                chosen_tweet,
                message_prefix="Guardado pese a similitud.",
            )
        except Exception as exc:
            logger.error("[CHAT_ID: %s] Error en confirmación: %s", chat_id, exc, exc_info=True)
            self.telegram.send_message(
                chat_id,
                "⚠️ No pude completar la confirmación. Genera uno nuevo.",
                reply_markup=self.telegram.get_new_tweet_keyboard(),
            )

    def _handle_copy(self, chat_id: int, action: CallbackAction) -> None:
        if not action.topic_id or not action.option:
            logger.warning("[CHAT_ID: %s] Callback copy incompleto: %s", chat_id, action.raw)
            self.telegram.send_message(chat_id, "⚠️ No pude localizar el borrador a copiar.")
            return
        try:
            payload = self.drafts.load(chat_id, action.topic_id)
            chosen_tweet = payload.get_variant(action.option)
            if not chosen_tweet:
                raise ValueError("Opción elegida no encontrada")
            body = (
                f"<b>Opción {action.option.upper()} (copiar)</b>\n"
                f"<pre>{self.telegram.escape(chosen_tweet)}</pre>"
            )
            self.telegram.send_message(chat_id, body, as_html=True)
        except Exception as exc:
            logger.error("[CHAT_ID: %s] Error al copiar opción %s: %s", chat_id, action.option, exc, exc_info=True)
            self.telegram.send_message(chat_id, "⚠️ No pude obtener el texto a copiar.")

    def _finalize_choice(
        self,
        chat_id: int,
        option: str,
        topic_id: str,
        chosen_tweet: str,
        message_prefix: Optional[str] = None,
    ) -> None:
        memory_collection = get_memory_collection()
        tweet_embedding = get_embedding(chosen_tweet)
        total_memory = None
        if tweet_embedding:
            memory_collection.add(embeddings=[tweet_embedding], documents=[chosen_tweet], ids=[topic_id])
            try:
                total_memory = memory_collection.count()
            except Exception:
                total_memory = None

        intent_url = f"{self.share_base_url}{quote(chosen_tweet, safe='') }"
        keyboard = {"inline_keyboard": [[{"text": f"🚀 Publicar Opción {option}", "url": intent_url}]]}
        if message_prefix:
            self.telegram.send_message(chat_id, message_prefix)
        self.telegram.send_message(chat_id, "Usa el siguiente botón para publicar:", reply_markup=keyboard)
        if total_memory is not None:
            self.telegram.send_message(chat_id, f"✅ Añadido a la memoria. Ya hay {total_memory} publicaciones.")
        self.telegram.send_message(chat_id, "Listo para el siguiente.", reply_markup=self.telegram.get_new_tweet_keyboard())
        self.drafts.delete(chat_id, topic_id)
