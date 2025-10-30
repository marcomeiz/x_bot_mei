import os
import threading
import time
from typing import Callable, Dict, Optional
from urllib.parse import quote

from callback_parser import CallbackAction, CallbackType, parse_callback
from core_generator import (
    find_relevant_topic,
    generate_tweet_from_topic,
    find_topic_by_id,
    generate_comment_from_text,
    CommentSkip,
)
from draft_repository import DraftPayload, DraftRepository
from embeddings_manager import get_embedding, get_memory_collection
from evaluation import evaluate_draft
from logger_config import logger
from prompt_context import build_prompt_context
from style_guard import StyleRejection
from telegram_client import TelegramClient


class ProviderGenerationError(Exception):
    """Raised cuando el proveedor LLM devuelve un error no recuperable (modelo, créditos, etc.)."""


MIN_LLM_WINDOW_SECONDS = float(os.getenv("LLM_MIN_WINDOW_SECONDS", "5") or 5.0)
JOB_TIMEOUT_MESSAGE = "⌛ Estoy al máximo ahora mismo. Intenta nuevamente en unos segundos."


class ProposalService:
    def __init__(
        self,
        telegram: TelegramClient,
        draft_repo: Optional[DraftRepository] = None,
        similarity_threshold: float = 0.20,
        job_scheduler: Optional[Callable[[int], None]] = None,
    ) -> None:
        self.telegram = telegram
        self.drafts = draft_repo or DraftRepository("/tmp")
        self.similarity_threshold = similarity_threshold
        self.share_base_url = os.getenv(
            "THREADS_SHARE_URL",
            "https://www.threads.net/intent/post?text=",
        )
        self.show_internal_summary = os.getenv("SHOW_INTERNAL_SUMMARY", "0").lower() in {"1", "true", "yes", "y"}
        self.job_scheduler = job_scheduler

    # ------------------------------------------------------------------ public
    def do_the_work(self, chat_id: int, deadline: Optional[float] = None) -> None:
        logger.info("[CHAT_ID: %s] Iniciando nuevo ciclo de generación.", chat_id)
        per_topic_gen_retries = int(os.getenv("GENERATION_RETRIES_PER_TOPIC", "1") or 1)

        def _deadline_exceeded() -> bool:
            return deadline is not None and time.monotonic() >= deadline

        if _deadline_exceeded():
            self.telegram.send_message(chat_id, JOB_TIMEOUT_MESSAGE)
            return

        topic = find_relevant_topic()
        if not topic:
            logger.warning("[CHAT_ID: %s] No hay temas disponibles para generar.", chat_id)
            self.telegram.send_message(
                chat_id,
                "❌ No encontré temas disponibles para generar en este momento.",
                reply_markup=self.telegram.get_new_tweet_keyboard(),
            )
            return

        for gen_try in range(1, per_topic_gen_retries + 2):  # +1 intento base
            if _deadline_exceeded():
                self.telegram.send_message(chat_id, JOB_TIMEOUT_MESSAGE)
                return
            try:
                if self.propose_tweet(chat_id, topic, deadline=deadline):
                    logger.info("[CHAT_ID: %s] Propuesta enviada correctamente.", chat_id)
                    return
            except ProviderGenerationError:
                logger.warning("[CHAT_ID: %s] Abortando reintentos: error del proveedor LLM.", chat_id)
                return
            logger.warning(
                "[CHAT_ID: %s] Generación fallida para el mismo tema (intento %s/%s).",
                chat_id,
                gen_try,
                per_topic_gen_retries + 1,
            )

        logger.warning("[CHAT_ID: %s] Generación fallida tras todos los reintentos. Permitiremos similitud para el mismo tema.", chat_id)
        if _deadline_exceeded():
            self.telegram.send_message(chat_id, JOB_TIMEOUT_MESSAGE)
            return
        try:
            if self.propose_tweet(chat_id, topic, ignore_similarity=True, deadline=deadline):
                logger.info("[CHAT_ID: %s] Propuesta enviada con similitud permitida para el mismo tema.", chat_id)
                return
        except ProviderGenerationError:
            logger.warning("[CHAT_ID: %s] Abortando intento adicional (ignorar similitud) por error del proveedor LLM.", chat_id)
            return

        self.telegram.send_message(
            chat_id,
            "❌ No pude generar propuesta para este tema. Inténtalo nuevamente en unos minutos.",
            reply_markup=self.telegram.get_new_tweet_keyboard(),
        )

    def generate_comment(self, chat_id: int, source_text: str) -> None:
        cleaned = (source_text or "").strip()
        if not cleaned:
            self.telegram.send_message(chat_id, "Necesito que pegues el texto de la publicación después de /comentar.")
            return

        snippet = self.telegram.clean_abstract(cleaned, max_len=180)
        pre_lines = [
            "💬 Preparando respuesta…",
            f"🗞️ Post: {snippet}",
        ]
        self.telegram.send_message(chat_id, "\n".join(pre_lines))

        try:
            comment_result = generate_comment_from_text(cleaned)
        except CommentSkip as skip_reason:
            extracted_reason = str(skip_reason).strip()
            reason = extracted_reason or getattr(skip_reason, "message", "") or "Sin ángulo claro para aportar valor."
            logger.info("[CHAT_ID: %s] Comentario omitido: %s", chat_id, reason)
            self.telegram.send_message(
                chat_id,
                f"🙅‍♂️ Mejor no comentar: {reason}",
            )
            return
        except StyleRejection as rejection:
            feedback = str(rejection).strip()
            logger.warning("[CHAT_ID: %s] Comentario rechazado por estilo: %s", chat_id, feedback)
            self.telegram.send_message(
                chat_id,
                "⚠️ El validador externo rechazó el comentario. Ajusta el texto o intenta de nuevo.",
            )
            return
        except Exception as exc:
            logger.error("[CHAT_ID: %s] Error generando comentario: %s", chat_id, exc, exc_info=True)
            self.telegram.send_message(
                chat_id,
                "❌ No pude generar un comentario ahora mismo. Inténtalo nuevamente en unos minutos.",
            )
            return

        context = build_prompt_context()
        evaluation = evaluate_draft(comment_result.comment, context)

        message = self.telegram.format_comment_message(
            reference_excerpt=snippet,
            comment_text=comment_result.comment,
            evaluation=evaluation,
            insight=comment_result.insight,
        )
        self.telegram.send_message(chat_id, message, as_html=True)

    def propose_tweet(
        self,
        chat_id: int,
        topic: Dict,
        ignore_similarity: bool = False,
        deadline: Optional[float] = None,
    ) -> bool:
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
        pre_lines.append("Generando 3 alternativas de longitud variable…")
        self.telegram.send_message(chat_id, "\n".join(pre_lines))

        def _deadline_reached() -> bool:
            return deadline is not None and time.monotonic() >= deadline

        variant_errors: Dict[str, str] = {}
        draft_a = ""
        draft_b = ""
        draft_c = ""

        def _process_generation_result(gen_result: Dict[str, object]) -> None:
            nonlocal draft_a, draft_b, draft_c, variant_errors
            if gen_result.get("provider_error"):
                reason = str(gen_result.get("error") or "El proveedor LLM devolvió un error.")
                self.telegram.send_message(
                    chat_id,
                    f"❌ Error del proveedor LLM: {reason}",
                    reply_markup=self.telegram.get_new_tweet_keyboard(),
                )
                raise ProviderGenerationError(reason)

            if "error" in gen_result and not any(
                gen_result.get(k) for k in ("short", "mid", "long")
            ):
                raise Exception(gen_result["error"])

            variant_errors = dict(gen_result.get("variant_errors", {}))
            draft_a = (gen_result.get("short") or "").strip()
            draft_b = (gen_result.get("mid") or "").strip()
            draft_c = (gen_result.get("long") or "").strip()

        if _deadline_reached():
            self.telegram.send_message(chat_id, JOB_TIMEOUT_MESSAGE)
            return False

        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or remaining < MIN_LLM_WINDOW_SECONDS:
                self.telegram.send_message(chat_id, JOB_TIMEOUT_MESSAGE)
                return False

        try:
            gen_result = generate_tweet_from_topic(topic_abstract, ignore_similarity=ignore_similarity)
            _process_generation_result(gen_result)

        except ProviderGenerationError:
            raise
        except Exception as e:
            msg = str(e)
            if _deadline_reached():
                self.telegram.send_message(chat_id, JOB_TIMEOUT_MESSAGE)
                return False
            if "wrong char range for 'short'" in msg or "wrong char range for 'mid'" in msg or "wrong char range for 'long'" in msg:
                logger.warning("[CHAT_ID: %s] Length issue detected (%s). Retrying generation once for the same topic…", chat_id, msg)
                try:
                    if deadline is not None:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0 or remaining < MIN_LLM_WINDOW_SECONDS:
                            self.telegram.send_message(chat_id, JOB_TIMEOUT_MESSAGE)
                            return False
                    gen_result = generate_tweet_from_topic(topic_abstract, ignore_similarity=ignore_similarity)
                    _process_generation_result(gen_result)
                except ProviderGenerationError:
                    raise
                except Exception as e2:
                    logger.error(f"[CHAT_ID: {chat_id}] Error after retry: {e2}", exc_info=True)
                    self.telegram.send_message(chat_id, f"❌ Ocurrió un error inesperado: {e2}")
                    return False
            else:
                logger.error(f"[CHAT_ID: {chat_id}] Error generating tweet from topic: {e}", exc_info=True)
                self.telegram.send_message(chat_id, f"❌ Ocurrió un error inesperado: {e}")
                return False

        available_a = bool(draft_a)
        available_b = bool(draft_b)
        available_c = bool(draft_c)

        if not (available_a or available_b or available_c):
            error_summary = variant_errors or {"all": "No se generaron variantes utilizables."}
            label_lookup = {"short": "A", "mid": "B", "long": "C", "all": "Todas"}
            summary_lines = [
                "❌ No pude generar variantes publicables para este tema."
            ] + [
                f"- {label_lookup.get(label, label.upper())}: {reason}"
                for label, reason in error_summary.items()
            ]
            self.telegram.send_message(
                chat_id,
                "\n".join(summary_lines),
                reply_markup=self.telegram.get_new_tweet_keyboard(),
            )
            return False

        # Labeling logic as per user suggestion
        def get_label(text: str) -> str:
            length = len(text)
            if length < 170: return "short"
            if 170 <= length <= 230: return "mid"
            return "long"

        labeled_drafts: Dict[str, str] = {}
        if draft_a:
            labeled_drafts[get_label(draft_a)] = draft_a
        if draft_b:
            labeled_drafts[get_label(draft_b)] = draft_b
        if draft_c:
            labeled_drafts[get_label(draft_c)] = draft_c

        payload = DraftPayload(
            draft_a=draft_a or None, 
            draft_b=draft_b or None, 
            draft_c=draft_c or None, 
            category="Multi-length"
        )
        self.drafts.save(chat_id, topic_id, payload)

        keyboard = self.telegram.build_proposal_keyboard(
            topic_id,
            has_variant_c=available_c,
            allow_variant_c=available_c,
            enable_a=available_a,
            enable_b=available_b,
        )

        evaluations = {}
        context = build_prompt_context()
        for i, draft in enumerate([draft_a, draft_b, draft_c]):
            if draft:
                evaluations[chr(65+i)] = evaluate_draft(draft, context)

        message_text = self.telegram.format_proposal_message(
            topic_id,
            topic_abstract or "",
            source_pdf,
            draft_a if draft_a else None,
            draft_b if draft_b else None,
            draft_c if draft_c else None,
            "Multi-length",
            evaluations=evaluations,
            labels=labeled_drafts,
            errors=variant_errors,
        )

        logger.info("[CHAT_ID: %s] Intentando enviar propuesta a Telegram.", chat_id)
        if self.telegram.send_message(chat_id, message_text, reply_markup=keyboard, as_html=True):
            logger.info("[CHAT_ID: %s] Propuesta enviada correctamente a Telegram.", chat_id)
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
        elif action.type == CallbackType.NOOP:
            logger.info("[CHAT_ID: %s] Acción noop ignorada.", chat_id)
        elif action.type == CallbackType.GENERATE:
            logger.info("[CHAT_ID: %s] Solicitud de nueva propuesta manual.", chat_id)
            self.telegram.edit_message(chat_id, message_id, "🚀 Buscando un nuevo tema…")
            if self.job_scheduler:
                self.job_scheduler(chat_id)
            else:
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
