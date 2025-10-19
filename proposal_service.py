import json
import os
import threading
from typing import Dict, Optional
from urllib.parse import quote_plus

from core_generator import (
    find_relevant_topic,
    generate_third_tweet_variant,
    generate_tweet_from_topic,
    find_topic_by_id,
)
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
        temp_dir: str = "/tmp",
        similarity_threshold: float = 0.20,
    ) -> None:
        self.telegram = telegram
        self.temp_dir = temp_dir
        self.similarity_threshold = similarity_threshold

    # --------------------------------------------------------------------- public
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

        draft_a, draft_b = generate_tweet_from_topic(topic_abstract, ignore_similarity=ignore_similarity)
        try:
            draft_c, category_name = generate_third_tweet_variant(topic_abstract)
        except StyleRejection as rejection:
            feedback = str(rejection).strip()
            logger.warning("[CHAT_ID: %s] Variante C rechazada: %s", chat_id, feedback)
            feedback_short = (feedback[:200] + "…") if len(feedback) > 200 else feedback
            draft_c = f"[Rejected by final reviewer: {feedback_short}]"
            category_name = "Rejected"

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

        if "Error: El tema es demasiado similar" in draft_a:
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

        temp_file_path = os.path.join(self.temp_dir, f"{chat_id}_{topic_id}.tmp")
        os.makedirs(self.temp_dir, exist_ok=True)
        with open(temp_file_path, "w") as handle:
            json.dump(
                {"draft_a": draft_a, "draft_b": draft_b, "draft_c": draft_c, "category": category_name},
                handle,
            )

        keyboard_rows = [
            [
                {"text": "👍 Aprobar A", "callback_data": f"approve_A_{topic_id}"},
                {"text": "👍 Aprobar B", "callback_data": f"approve_B_{topic_id}"},
            ],
            [
                {"text": "📋 Copiar A", "callback_data": f"copy_A_{topic_id}"},
                {"text": "📋 Copiar B", "callback_data": f"copy_B_{topic_id}"},
            ],
        ]

        if draft_c:
            keyboard_rows.append([
                {"text": "👍 Aprobar C", "callback_data": f"approve_C_{topic_id}"},
            ])
            keyboard_rows.append([
                {"text": "📋 Copiar C", "callback_data": f"copy_C_{topic_id}"},
            ])

        keyboard_rows.append([{"text": "👎 Rechazar Todos", "callback_data": f"reject_{topic_id}"}])
        keyboard_rows.append([{"text": "🔁 Generar Nuevo", "callback_data": "generate_new"}])

        keyboard = {"inline_keyboard": keyboard_rows}

        if category_name == "Rejected" and draft_c:
            keyboard_rows[2][0] = {"text": "⚠️ C Rechazada", "callback_data": "noop"}
            # remove copy button for C
            keyboard_rows.pop(3)

        message_text = self.telegram.format_proposal_message(
            topic_id,
            topic_abstract or "",
            source_pdf,
            draft_a,
            draft_b,
            draft_c,
            category_name,
        )
        evaluation_section = self.telegram.build_evaluation_section(evaluations)
        if evaluation_section:
            message_text = f"{message_text}{evaluation_section}"

        if self.telegram.send_message(chat_id, message_text, reply_markup=keyboard, as_html=True):
            return True
        logger.error("[CHAT_ID: %s] Falló el envío de propuestas para topic %s.", chat_id, topic_id)
        return False

    def handle_callback_query(self, update: Dict) -> None:
        query = update.get("callback_query", {})
        chat_id = query["message"]["chat"]["id"]
        message_id = query["message"]["message_id"]
        callback_data = query.get("data", "")
        logger.info("[CHAT_ID: %s] Callback recibido: '%s'", chat_id, callback_data)

        parts = callback_data.split("_", 2)
        action = parts[0] if parts else ""
        option = parts[1] if len(parts) >= 2 and action == "approve" else ""
        topic_id = parts[2] if len(parts) == 3 else (parts[1] if len(parts) == 2 else "")

        original_message_text = query["message"].get("text", "")

        if action == "approve":
            self._handle_approve(chat_id, message_id, topic_id, option, original_message_text)
        elif action == "confirm":
            self._handle_confirm(chat_id, topic_id, option)
        elif action == "cancel":
            logger.info("[CHAT_ID: %s] Confirmación cancelada para topic %s.", chat_id, topic_id)
            self.telegram.send_message(chat_id, "Operación cancelada.", reply_markup=self.telegram.get_new_tweet_keyboard())
        elif action == "reject":
            logger.info("[CHAT_ID: %s] Ambas opciones rechazadas para topic %s.", chat_id, topic_id)
            appended = "❌ <b>Rechazados.</b>"
            new_text = (original_message_text or "") + "\n\n" + appended
            self.telegram.edit_message(
                chat_id,
                message_id,
                new_text,
                reply_markup=self.telegram.get_new_tweet_keyboard(),
                as_html=True,
            )
        elif action == "copy":
            self._handle_copy(chat_id, topic_id, option)
        elif action == "noop":
            logger.info("[CHAT_ID: %s] Acción noop ignorada.", chat_id)
        elif "generate" in callback_data:
            logger.info("[CHAT_ID: %s] Solicitud de nueva propuesta manual.", chat_id)
            self.telegram.edit_message(chat_id, message_id, "🚀 Buscando un nuevo tema…")
            threading.Thread(target=self.do_the_work, args=(chat_id,)).start()

    # ---------------------------------------------------------------- utilities
    def _load_temp_tweets(self, chat_id: int, topic_id: str) -> Dict[str, str]:
        temp_file_path = os.path.join(self.temp_dir, f"{chat_id}_{topic_id}.tmp")
        if not os.path.exists(temp_file_path):
            raise FileNotFoundError(f"Temp file missing: {temp_file_path}")
        with open(temp_file_path, "r") as handle:
            return json.load(handle)

    def _handle_approve(self, chat_id: int, message_id: int, topic_id: str, option: str, original_text: str) -> None:
        appended = f"✅ <b>{self.telegram.escape(f'¡Aprobada Opción {option}!')}</b>"
        new_text = (original_text or "") + "\n\n" + appended
        self.telegram.edit_message(chat_id, message_id, new_text, as_html=True)
        try:
            tweets = self._load_temp_tweets(chat_id, topic_id)
            chosen_tweet = tweets.get(f"draft_{option.lower()}", "")
            if not chosen_tweet:
                raise ValueError("Opción elegida no encontrada")

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
                                {"text": "✅ Confirmar", "callback_data": f"confirm_{option}_{topic_id}"},
                                {"text": "❌ Cancelar", "callback_data": "cancel"},
                            ]
                        ]
                    }
                    self.telegram.send_message(chat_id, warn, reply_markup=keyboard)
                    return

            self._finalize_choice(chat_id, option, topic_id, chosen_tweet)
        except Exception as exc:
            logger.error("[CHAT_ID: %s] Error crítico en aprobación: %s", chat_id, exc, exc_info=True)
            self.telegram.send_message(
                chat_id,
                "⚠️ No pude recuperar el borrador aprobado (quizá expiró). Genera uno nuevo con el botón.",
                reply_markup=self.telegram.get_new_tweet_keyboard(),
            )

    def _handle_confirm(self, chat_id: int, topic_id: str, option: str) -> None:
        logger.info("[CHAT_ID: %s] Confirmación recibida para opción %s (%s).", chat_id, option, topic_id)
        try:
            tweets = self._load_temp_tweets(chat_id, topic_id)
            chosen_tweet = tweets.get(f"draft_{option.lower()}", "")
            if not chosen_tweet:
                raise ValueError("Opción elegida no encontrada")
            self._finalize_choice(chat_id, option, topic_id, chosen_tweet, message_prefix="Guardado pese a similitud.")
        except Exception as exc:
            logger.error("[CHAT_ID: %s] Error en confirmación: %s", chat_id, exc, exc_info=True)
            self.telegram.send_message(
                chat_id,
                "⚠️ No pude completar la confirmación. Genera uno nuevo.",
                reply_markup=self.telegram.get_new_tweet_keyboard(),
            )

    def _handle_copy(self, chat_id: int, topic_id: str, option: str) -> None:
        logger.info("[CHAT_ID: %s] Solicitud de copia para opción %s (%s).", chat_id, option, topic_id)
        try:
            tweets = self._load_temp_tweets(chat_id, topic_id)
            chosen_tweet = tweets.get(f"draft_{option.lower()}", "")
            if not chosen_tweet:
                raise ValueError("Opción elegida no encontrada")
            body = (
                f"<b>Opción {option.upper()} (copiar)</b>\n"
                f"<pre>{self.telegram.escape(chosen_tweet)}</pre>"
            )
            self.telegram.send_message(chat_id, body, as_html=True)
        except Exception as exc:
            logger.error("[CHAT_ID: %s] Error al copiar opción %s: %s", chat_id, option, exc, exc_info=True)
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

        intent_url = f"https://x.com/intent/tweet?text={quote_plus(chosen_tweet)}"
        keyboard = {"inline_keyboard": [[{"text": f"🚀 Publicar Opción {option}", "url": intent_url}]]}
        if message_prefix:
            self.telegram.send_message(chat_id, message_prefix)
        self.telegram.send_message(chat_id, "Usa el siguiente botón para publicar:", reply_markup=keyboard)
        if total_memory is not None:
            self.telegram.send_message(chat_id, f"✅ Añadido a la memoria. Ya hay {total_memory} publicaciones.")
        self.telegram.send_message(chat_id, "Listo para el siguiente.", reply_markup=self.telegram.get_new_tweet_keyboard())
