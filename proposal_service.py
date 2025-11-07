import os
import re
import json
from typing import Optional
import json
import threading
import time
from itertools import combinations
from typing import Callable, Dict, Optional, Tuple
from urllib.parse import quote

from callback_parser import CallbackAction, CallbackType, parse_callback
from core_generator import (
    generate_tweet_from_topic,
    find_topic_by_id,
    generate_comment_from_text,
    CommentSkip,
)
from src.topics_repo import get_topic_or_fallback
from draft_repository import DraftPayload, DraftRepository
from embeddings_manager import get_embedding, get_memory_collection
from evaluation import evaluate_draft
from logger_config import logger
from prompt_context import build_prompt_context
from persona import get_style_contract_text
from src.prompt_loader import load_prompt, PromptSpec
from persona import get_style_contract_text
from src.prompt_loader import load_prompt
from style_guard import StyleRejection
from telegram_client import TelegramClient
from llm_fallback import llm
from src.messages import get_message
from src.goldset import (
    GOLDSET_MIN_SIMILARITY,
    get_active_goldset_collection_name,
    get_goldset_similarity_details,
)
from src.settings import AppSettings
from diagnostics_logger import log_post_metrics
from metrics import Timer, record_metric


class ProviderGenerationError(Exception):
    """Raised cuando el proveedor LLM devuelve un error no recuperable (modelo, créditos, etc.)."""


MIN_LLM_WINDOW_SECONDS = float(os.getenv("LLM_MIN_WINDOW_SECONDS", "5") or 5.0)
JOB_TIMEOUT_MESSAGE = get_message("job_timeout")
VARIANT_SIMILARITY_THRESHOLD = float(os.getenv("VARIANT_SIMILARITY_THRESHOLD", "0.78") or 0.78)
LOG_GENERATED_VARIANTS = os.getenv("LOG_GENERATED_VARIANTS", "1").lower() in {"1", "true", "yes"}


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
        settings = AppSettings.load()
        self.refiner_model = settings.post_refiner_model

    # ------------------------------------------------------------------ public
    def do_the_work(self, chat_id: int, deadline: Optional[float] = None, model_override: Optional[str] = None) -> None:
        logger.info("[CHAT_ID: %s] Iniciando nuevo ciclo de generación. Model: %s", chat_id, model_override or "default")
        per_topic_gen_retries = int(os.getenv("GENERATION_RETRIES_PER_TOPIC", "1") or 1)

        def _deadline_exceeded() -> bool:
            return deadline is not None and time.monotonic() >= deadline

        if _deadline_exceeded():
            self.telegram.send_message(chat_id, JOB_TIMEOUT_MESSAGE)
            return

        # Seleccionar tema: usar repositorio con fallback (nunca None)
        with Timer("g_find_topic", labels={"chat_id": chat_id}):
            selected = get_topic_or_fallback(str(chat_id))
        # Normalizar al contrato esperado por propose_tweet
        topic = {
            "abstract": selected.get("text"),
            "topic_id": selected.get("id"),
            "source_pdf": None,
            "source": selected.get("source"),
        }
        logger.info(
            "[CHAT_ID: %s] Fuente del tema: %s (ID: %s)",
            chat_id,
            topic.get("source"),
            topic.get("topic_id"),
        )

        for gen_try in range(1, per_topic_gen_retries + 2):  # +1 intento base
            if _deadline_exceeded():
                self.telegram.send_message(chat_id, JOB_TIMEOUT_MESSAGE)
                return
            try:
                if self.propose_tweet(chat_id, topic, deadline=deadline, model_override=model_override):
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
            if self.propose_tweet(chat_id, topic, ignore_similarity=True, deadline=deadline, model_override=model_override):
                logger.info("[CHAT_ID: %s] Propuesta enviada con similitud permitida para el mismo tema.", chat_id)
                return
        except ProviderGenerationError:
            logger.warning("[CHAT_ID: %s] Abortando intento adicional (ignorar similitud) por error del proveedor LLM.", chat_id)
            return

        self.telegram.send_message(
            chat_id,
            get_message("proposal_generation_failed"),
            reply_markup=self.telegram.get_new_tweet_keyboard(),
        )

    def generate_comment(self, chat_id: int, source_text: str) -> None:
        cleaned = (source_text or "").strip()
        if not cleaned:
            self.telegram.send_message(chat_id, get_message("comment_missing_text"))
            return

        snippet = self.telegram.clean_abstract(cleaned, max_len=180)
        pre_lines = [
            get_message("comment_preparing"),
            get_message("comment_post_snippet", snippet=snippet),
        ]
        self.telegram.send_message(chat_id, "\n".join(pre_lines))

        try:
            comment_result = generate_comment_from_text(cleaned)
        except CommentSkip as skip_reason:
            extracted_reason = str(skip_reason).strip()
            raw_reason = extracted_reason or getattr(skip_reason, "message", "") or get_message("comment_skip_default_reason")
            reason = raw_reason.replace("{", "{{").replace("}", "}}")
            logger.info("[CHAT_ID: %s] Comentario omitido: %s", chat_id, raw_reason)
            self.telegram.send_message(
                chat_id,
                get_message("comment_skip", reason=reason),
            )
            return
        except StyleRejection as rejection:
            feedback = str(rejection).strip()
            logger.warning("[CHAT_ID: %s] Comentario rechazado por estilo: %s", chat_id, feedback)
            self.telegram.send_message(
                chat_id,
                get_message("comment_style_rejected"),
            )
            return
        except Exception as exc:
            logger.error("[CHAT_ID: %s] Error generando comentario: %s", chat_id, exc, exc_info=True)
            self.telegram.send_message(
                chat_id,
                get_message("comment_error"),
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
        model_override: Optional[str] = None,
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

        abstract_clean = self.telegram.clean_abstract(topic_abstract) if topic_abstract else ""
        abstract_display = f"{abstract_clean[:80]}…" if abstract_clean else ""
        pre_lines = [get_message("selecting_topic")]
        pre_lines.append(get_message("topic_label", abstract=abstract_display))
        if source_pdf:
            pre_lines.append(get_message("topic_origin", source=source_pdf))
        pre_lines.append(get_message("generating_variants"))
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
                reason = str(gen_result.get("error") or "")
                self.telegram.send_message(
                    chat_id,
                    get_message("provider_error", reason=reason or "Error desconocido."),
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
            with Timer("g_generate_variants", labels={"chat_id": chat_id}):
                gen_result = generate_tweet_from_topic(topic_abstract, ignore_similarity=ignore_similarity, model_override=model_override)
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
                    gen_result = generate_tweet_from_topic(topic_abstract, ignore_similarity=ignore_similarity, model_override=model_override)
                    _process_generation_result(gen_result)
                except ProviderGenerationError:
                    raise
                except Exception as e2:
                    logger.error(f"[CHAT_ID: {chat_id}] Error after retry: {e2}", exc_info=True)
                    self.telegram.send_message(chat_id, get_message("unexpected_error", error=e2))
                    return False
            else:
                logger.error(f"[CHAT_ID: {chat_id}] Error generating tweet from topic: {e}", exc_info=True)
                self.telegram.send_message(chat_id, get_message("unexpected_error", error=e))
                return False

        if LOG_GENERATED_VARIANTS:
            preview_map = {"A": draft_a, "B": draft_b, "C": draft_c}
            for label, text in preview_map.items():
                cleaned = (text or "").strip()
                logger.info(
                    "[CHAT_ID: %s] Draft %s pre-check (%s chars):\n%s",
                    chat_id,
                    label,
                    len(cleaned),
                    cleaned or "<vacío>",
                )

        similar, pair_info = self._check_variant_similarity({
            "A": draft_a,
            "B": draft_b,
            "C": draft_c,
        })
        if similar:
            labels = " y ".join(pair_info[:2]) if pair_info else ""
            sim_value = pair_info[2] if pair_info else 0.0
            logger.warning(
                "[CHAT_ID: %s] Variantes demasiado similares (%s, sim=%.2f). Regenerando…",
                chat_id,
                labels,
                sim_value,
            )
            with Timer("g_send_warning_variants_similar", labels={"chat_id": chat_id}):
                self.telegram.send_message(chat_id, get_message("variants_similar_initial"))
            return False

        with Timer("g_check_contract_style_llm", labels={"chat_id": chat_id}):
            check_results_pre = self._check_contract_requirements(
                {
                    "short": draft_a,
                    "mid": draft_b,
                    "long": draft_c,
                },
                piece_id=topic_id,
                log_stage="PRE",
            )
        at_least_one_passed_pre = bool(check_results_pre) and any(check_results_pre)
        if not at_least_one_passed_pre:
            if ignore_similarity:
                self.telegram.send_message(
                    chat_id,
                    get_message("contract_failure"),
                    reply_markup=self.telegram.get_new_tweet_keyboard(),
                )
                return False
            with Timer("g_send_warning_contract_retry", labels={"chat_id": chat_id}):
                self.telegram.send_message(chat_id, get_message("contract_retry"))
            return False
        if at_least_one_passed_pre:
            logger.info("[CONTROL] Al menos un draft pasó la validación (PRE). Enviando al usuario.")

        available_a = bool(draft_a)
        available_b = bool(draft_b)
        available_c = bool(draft_c)

        if not (available_a or available_b or available_c):
            error_summary = variant_errors or {"all": get_message("variant_error_default")}
            label_lookup = {"short": "A", "mid": "B", "long": "C", "all": "Todas"}
            summary_lines = [
                get_message("variant_failure_header")
            ] + [
                f"- {label_lookup.get(label, label.upper())}: {reason}"
                for label, reason in error_summary.items()
            ]
            with Timer("g_send_error_variants_summary", labels={"chat_id": chat_id}):
                self.telegram.send_message(
                chat_id,
                "\n".join(summary_lines),
                reply_markup=self.telegram.get_new_tweet_keyboard(),
            )
            return False

        # The 'labeled_drafts' dictionary maps the generation labels (short, mid, long)
        # to the corresponding draft content. This ensures consistency with the
        # generation process, where draft_a is 'short', draft_b is 'mid', etc.
        labeled_drafts: Dict[str, str] = {}
        if draft_a:
            labeled_drafts["short"] = draft_a
        if draft_b:
            labeled_drafts["mid"] = draft_b
        if draft_c:
            labeled_drafts["long"] = draft_c

        payload = DraftPayload(
            draft_a=draft_a or None, 
            draft_b=draft_b or None, 
            draft_c=draft_c or None, 
            category="Multi-length"
        )
        with Timer("g_save_draft_payload", labels={"chat_id": chat_id}):
            self.drafts.save(chat_id, topic_id, payload)

        keyboard = self.telegram.build_proposal_keyboard(
            topic_id,
            has_variant_c=available_c,
            allow_variant_c=available_c,
            enable_a=available_a,
            enable_b=available_b,
        )

        draft_map = {"A": draft_a, "B": draft_b, "C": draft_c}
        if LOG_GENERATED_VARIANTS:
            for label, text in draft_map.items():
                logger.info("[CHAT_ID: %s] Draft %s generated:\n%s", chat_id, label, text or "<vacío>")

        message_text = self.telegram.format_proposal_message(
            topic_id,
            topic_abstract or "",
            source_pdf,
            draft_map["A"] if draft_map["A"] else None,
            draft_map["B"] if draft_map["B"] else None,
            draft_map["C"] if draft_map["C"] else None,
            "Multi-length",
            evaluations={},
            labels=labeled_drafts,
            errors=variant_errors,
        )

        # Add LLM Judge validation summary to the message
        if check_results_pre:
            judge_labels = ["A", "B", "C"]
            summary_parts = [f"{label}: {'✅' if passed else '❌'}" for label, passed in zip(judge_labels, check_results_pre)]
            summary_line = " | ".join(summary_parts)
            message_text += f"\n\n<b>LLM Judge:</b> {summary_line}"

        # Resumen final de variantes
        try:
            ordered = [k for k in ("A", "B", "C") if draft_map.get(k)]
            lines = [f"  - {k} | {draft_map.get(k)}" for k in ordered]
            if lines:
                logger.info("[SUMMARY]\n%s", "\n".join(lines))
        except Exception:
            logger.debug("Resumen de variantes no disponible (error de formateo).")

        logger.info("[CHAT_ID: %s] Intentando enviar propuesta a Telegram.", chat_id)
        with Timer("g_send_proposal", labels={"chat_id": chat_id}):
            sent_ok = self.telegram.send_message(chat_id, message_text, reply_markup=keyboard, as_html=True)
        if sent_ok:
            logger.info("[CHAT_ID: %s] Propuesta enviada correctamente a Telegram.", chat_id)
            try:
                # Successful send: log final metrics snapshot
                log_post_metrics(
                    chat_id=chat_id,
                    topic=topic,
                    drafts=draft_map,
                    evaluations={},
                    blocked=False,
                )
            except Exception:
                logger.debug("Diag logging (success snapshot) skipped due to an error.")
            return True
        logger.error("[CHAT_ID: %s] Falló el envío de propuestas para topic %s.", chat_id, topic_id)
        try:
            log_post_metrics(
                chat_id=chat_id,
                topic=topic,
                drafts=draft_map,
                evaluations=normalized_evals,
                blocked=True,
                blocking_reason="telegram_send_failed",
            )
        except Exception:
            logger.debug("Diag logging (send failure) skipped due to an error.")
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
            self.telegram.send_message(chat_id, get_message("operation_cancelled"), reply_markup=self.telegram.get_new_tweet_keyboard())
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
    def _normalize_evaluations(self, evaluations: Dict[str, Dict[str, object]]) -> Dict[str, Dict[str, object]]:
        normalized: Dict[str, Dict[str, object]] = {}
        for label, payload in evaluations.items():
            if not isinstance(payload, dict):
                continue
            if payload.get("error"):
                normalized[label] = {
                    "summary": str(payload["error"]),
                    "needs_revision": True,
                }
                continue

            fast_eval = payload.get("fast_eval") if isinstance(payload.get("fast_eval"), dict) else {}
            slow_eval = payload.get("slow_eval") if isinstance(payload.get("slow_eval"), dict) else {}

            style_score = fast_eval.get("style_score")
            clarity_score = fast_eval.get("clarity_score")
            under_limit = fast_eval.get("is_under_limit")
            fast_summary = str(fast_eval.get("summary", "")).strip()
            slow_summary = str(slow_eval.get("summary", "")).strip()
            combined_summary = " ".join(part for part in (fast_summary, slow_summary) if part).strip()

            factuality = slow_eval.get("factuality")
            contrarian_score = slow_eval.get("contrarian_score")
            brand_fit_score = slow_eval.get("brand_fit_score")

            analysis_parts = []
            if isinstance(clarity_score, (int, float)):
                analysis_parts.append(f"Clarity {clarity_score}/5")
            if isinstance(contrarian_score, (int, float)):
                analysis_parts.append(f"Contrarian {contrarian_score}/5")
            if isinstance(brand_fit_score, (int, float)):
                analysis_parts.append(f"Brand {brand_fit_score}/10")
            if under_limit is False:
                analysis_parts.append("Length > limit")

            needs_revision = False
            if isinstance(style_score, (int, float)) and style_score < 4:
                needs_revision = True
            if isinstance(clarity_score, (int, float)) and clarity_score < 4:
                needs_revision = True
            if isinstance(contrarian_score, (int, float)) and contrarian_score < 3:
                needs_revision = True
            if isinstance(brand_fit_score, (int, float)) and brand_fit_score < 7:
                needs_revision = True
            if isinstance(factuality, str) and factuality.lower() in {"risky", "unclear"}:
                needs_revision = True

            normalized[label] = {
                "style_score": style_score,
                "factuality": factuality,
                "summary": combined_summary or None,
                "needs_revision": needs_revision,
            }
            if analysis_parts:
                normalized[label]["analysis"] = [{"comment": "; ".join(analysis_parts)}]
        return normalized

    def _check_variant_similarity(self, drafts: Dict[str, str]) -> Tuple[bool, Tuple[str, str, float]]:
        best_pair: Tuple[str, str, float] = ("", "", 0.0)
        for (label_a, text_a), (label_b, text_b) in combinations(drafts.items(), 2):
            text_a = (text_a or "").strip()
            text_b = (text_b or "").strip()
            if not text_a or not text_b:
                continue
            tokens_a = set(re.findall(r"\b[\w']+\b", text_a.lower()))
            tokens_b = set(re.findall(r"\b[\w']+\b", text_b.lower()))
            if not tokens_a or not tokens_b:
                continue
            intersection = tokens_a & tokens_b
            union = tokens_a | tokens_b
            similarity = len(intersection) / len(union) if union else 0.0
            if similarity >= VARIANT_SIMILARITY_THRESHOLD:
                return True, (label_a, label_b, similarity)
            if similarity > best_pair[2]:
                best_pair = (label_a, label_b, similarity)
        return False, best_pair

    def _check_contract_requirements(
        self,
        drafts: Dict[str, str],
        piece_id: Optional[str] = None,
        variant_sources: Optional[Dict[str, str]] = None,
        log_stage: str = "contract_check",
    ) -> list[bool]:
        """
        Nuevo juez de estilo (LLM) en modo BULK: valida todos los borradores en una sola llamada.
        """
        try:
            evaluations = self._check_style_with_llm_bulk(
                drafts,
                piece_id=piece_id,
                event_stage=log_stage,
                variant_sources=variant_sources,
            )
            # Devuelve una lista de booleanos `cumple_contrato` en el orden correcto
            ordered_labels = ["short", "mid", "long"]
            norm_map = {"A": "short", "B": "mid", "C": "long"}
            
            # Mapa para buscar si la evaluación pasó
            passed_map = {e["variant"]: e.get("passed", False) for e in evaluations}
            
            # Reconstruir la lista de resultados en el orden esperado por el llamador
            results = []
            for key in ordered_labels:
                # Considerar tanto la etiqueta normalizada (short) como la original (A)
                original_label = next((k for k, v in norm_map.items() if v == key), key)
                if drafts.get(original_label) or drafts.get(key):
                     results.append(passed_map.get(key, False))
            return results

        except Exception as e:
            logger.error("[JUDGE] Fallo crítico en la evaluación bulk: %s", e, exc_info=True)
            # Si la evaluación bulk falla, asumimos que todos fallan para forzar un reintento.
            return [False] * len([d for d in drafts.values() if d])

    def _check_style_with_llm_bulk(
        self,
        drafts: Dict[str, str],
        *,
        piece_id: Optional[str] = None,
        event_stage: Optional[str] = None,
        variant_sources: Optional[Dict[str, str]] = None,
    ) -> list[dict]:
        """
        Juez-Calificador (Grader) de estilo en modo BULK. Llama al LLM una vez para
        evaluar todos los borradores y loggear los resultados.
        """
        s = AppSettings.load()
        prompts_dir = s.prompts_dir
        contract_text = get_style_contract_text()
        spec = load_prompt(prompts_dir, "validation/style_judge_bulk_v1")

        # Mapeo de A/B/C a short/mid/long para asegurar que los drafts correctos se pasan al prompt
        norm_map = {"A": "short", "B": "mid", "C": "long"}
        draft_short = drafts.get(norm_map.get("A")) or drafts.get("short", "")
        draft_mid = drafts.get(norm_map.get("B")) or drafts.get("mid", "")
        draft_long = drafts.get(norm_map.get("C")) or drafts.get("long", "")

        user_text = spec.render(
            style_contract_text=contract_text,
            draft_short=draft_short,
            draft_mid=draft_mid,
            draft_long=draft_long,
        )

        # Extraer system prompt del template
        sys_match = re.search(r"<SYSTEM_PROMPT>\\s*([\\s\\S]*?)\\s*</SYSTEM_PROMPT>", spec.template, re.IGNORECASE)
        system_text = (sys_match.group(1) or "").strip() if sys_match else "Eres un editor de estilo de élite."

        model = s.eval_fast_model
        payload = llm.chat_json(
            model=model,
            messages=[
                {"role": "system", "content": system_text},
                {"role": "user", "content": user_text},
            ],
            temperature=0.1,
        )

        if not isinstance(payload, dict) or "evaluations" not in payload or not isinstance(payload["evaluations"], list):
            raise ValueError("La respuesta del LLM Juez (bulk) no tiene el formato esperado.")

        results = []
        all_evals = payload["evaluations"]
        
        # Crear un mapa para buscar borradores por etiqueta de variante normalizada
        draft_map = {
            "short": draft_short,
            "mid": draft_mid,
            "long": draft_long,
        }

        for eval_data in all_evals:
            variant_label = eval_data.get("variant")
            if not variant_label:
                continue

            passed = bool(eval_data.get("cumple_contrato", False))
            reason = str(eval_data.get("razonamiento_principal") or "").strip() or None
            
            # Loggear la métrica para cada variante
            try:
                log_post_metrics(
                    piece_id,
                    str(variant_label),
                    draft_map.get(variant_label, ""),
                    None,
                    0.0,
                    passed,
                    event_stage=event_stage,
                    variant_source=(variant_sources or {}).get(variant_label),
                    judge_reasoning=reason,
                    judge_tono=eval_data.get("puntuacion_tono"),
                    judge_diccion=eval_data.get("puntuacion_diccion"),
                    judge_ritmo=eval_data.get("puntuacion_ritmo"),
                )
            except Exception:
                logger.debug("[JUDGE] Diag logging (bulk) para variante '%s' omitido por error.", variant_label)
            
            results.append({"variant": variant_label, "passed": passed, "reason": reason})
            
        return results

    def _should_refine_variant(self, evaluation: Optional[Dict[str, object]], text: str) -> bool:
        if not evaluation or not isinstance(evaluation, dict):
            return False
        if not text or not text.strip():
            return False
        if evaluation.get("needs_revision"):
            return True
        style = evaluation.get("style_score")
        if isinstance(style, (int, float)) and style < 4:
            return True
        summary = str(evaluation.get("summary") or "").lower()
        if any(keyword in summary for keyword in {"vague", "blando", "generic", "soft"}):
            return True
        return False

    def _refine_variant(
        self,
        label: str,
        original: str,
        evaluation: Dict[str, object],
        topic_abstract: str,
        context,
        other_variants: Dict[str, str],
    ) -> Optional[str]:
        model = getattr(self, "refiner_model", None)
        if not model or not original or not original.strip():
            return None
        try:
            summary = str(evaluation.get("summary") or "").strip()
            analysis = "".join(
                f"- {item.get('comment')}\n" for item in evaluation.get("analysis", []) if isinstance(item, dict)
            )
            others_text = "\n".join(
                f"{lbl}: {txt}" for lbl, txt in other_variants.items() if lbl != label and txt
            )
            system_message = (
                "You are a senior rewrite specialist. Maintain Alex Hormozi voice exactly as defined. "
                "Keep sentences short, direct, second-person, contract-compliant."
            )
            user_prompt = (
                "CONTRACT (excerpt):\n"
                f"{context.contract}\n\n"
                "ICP:\n"
                f"{context.icp}\n\n"
                "FINAL REVIEW GUIDELINES:\n"
                f"{context.final_guidelines}\n\n"
                "TOPIC ABSTRACT:\n"
                f"{topic_abstract}\n\n"
                "THIS VARIANT (label {label}):\n"
                f"{original}\n\n"
                "OTHER VARIANTS FOR DIVERSITY (read-only):\n"
                f"{others_text or 'N/A'}\n\n"
                "EVALUATION SUMMARY:\n"
                f"{summary or 'N/A'}\n"
                f"{analysis or ''}\n"
                "Rewrite the variant to: \n"
                "- Add specific proof (numbers, thresholds, vivid example) if missing.\n"
                "- Maintain second-person voice.\n"
                "- Keep 3-5 sentences max, each on its own line.\n"
                "- Stay under 280 characters.\n"
                "- Avoid repeating phrases used in other variants.\n"
                "Return only the rewritten text."
            )
            response = llm.chat_text(
                model=model,
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.35,
            )
            refined = (response or "").strip()
            if refined and len(refined) <= 280:
                return refined
        except Exception as exc:
            logger.warning("[REFINE] Error al reescribir variante %s: %s", label, exc)
        return None

    def _handle_approve(
        self,
        chat_id: int,
        message_id: int,
        action: CallbackAction,
        original_text: str,
    ) -> None:
        if not action.topic_id or not action.option:
            logger.warning("[CHAT_ID: %s] Callback approve incompleto: %s", chat_id, action.raw)
            self.telegram.send_message(chat_id, get_message("draft_not_found"))
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
                get_message("draft_storage_missing"),
                reply_markup=self.telegram.get_new_tweet_keyboard(),
            )
            return

        chosen_tweet = payload.get_variant(action.option)
        if not chosen_tweet:
            logger.error("[CHAT_ID: %s] Opción %s no encontrada en draft %s.", chat_id, action.option, action.topic_id)
            self.telegram.send_message(chat_id, get_message("option_not_available"))
            return

        memory_collection = get_memory_collection()
        # Política cache-only: no generar embeddings si faltan en memoria
        tweet_embedding = get_embedding(chosen_tweet, generate_if_missing=False)
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
                warn = get_message(
                    "similarity_warning",
                    distance=distance_value,
                    threshold=self.similarity_threshold,
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
            self.telegram.send_message(chat_id, get_message("confirm_failure"))
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
                message_prefix=get_message("manual_confirmation_prefix"),
            )
        except Exception as exc:
            logger.error("[CHAT_ID: %s] Error en confirmación: %s", chat_id, exc, exc_info=True)
            self.telegram.send_message(
                chat_id,
                get_message("confirm_failure"),
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
        # Al aprobar, permitimos generar embedding para persistir en memoria
        with Timer("g_embed_memory_on_approval", labels={"chat_id": chat_id}):
            tweet_embedding = get_embedding(chosen_tweet, generate_if_missing=True)
        total_memory = None
        if tweet_embedding:
            with Timer("g_memory_add", labels={"chat_id": chat_id}):
                memory_collection.add(embeddings=[tweet_embedding], documents=[chosen_tweet], ids=[topic_id])
            try:
                total_memory = memory_collection.count()
            except Exception:
                total_memory = None

        # Reportar el umbral de similitud del goldset para la versión aprobada
        try:
            with Timer("g_goldset_threshold_on_approval", labels={"chat_id": chat_id, "option": option}):
                sim_details = get_goldset_similarity_details(chosen_tweet, generate_if_missing=True)
            active_collection = get_active_goldset_collection_name()
            sim_value = sim_details.similarity
            min_required = GOLDSET_MIN_SIMILARITY
            passed_flag = (sim_value is not None and sim_value >= min_required)
            obtained_text = "NA" if sim_value is None else f"{sim_value:.3f}"
            status_text = "Sí" if passed_flag else "No"
            # Emitir también como métrica para trazabilidad
            try:
                record_metric(
                    name="g_goldset_threshold_report",
                    value=float(sim_value) if sim_value is not None else -1.0,
                    labels={
                        "option": option,
                        "topic_id": topic_id,
                        "passed": str(passed_flag),
                        "min_required": f"{min_required:.2f}",
                        "goldset_collection": active_collection or "unknown",
                    },
                )
            except Exception:
                # No bloquear por fallos de métrica
                pass
            # Mensaje a Telegram
            threshold_lines = [
                f"📏 Umbral (goldset) para Opción {option}",
                f"• obtenido: {obtained_text}",
                f"• mínimo requerido: {min_required:.2f}",
                f"• pasó: {status_text}",
                f"• colección activa: {active_collection}",
            ]
            with Timer("g_send_threshold_report", labels={"chat_id": chat_id}):
                self.telegram.send_message(chat_id, "\n".join(threshold_lines))
        except Exception:
            # Si falla la obtención de similitud, continuamos con el flujo normal
            pass

        intent_url = f"{self.share_base_url}{quote(chosen_tweet, safe='') }"
        keyboard = {"inline_keyboard": [[{"text": f"🚀 Publicar Opción {option}", "url": intent_url}]]}
        if message_prefix:
            with Timer("g_send_publish_prefix", labels={"chat_id": chat_id}):
                self.telegram.send_message(chat_id, message_prefix)
        with Timer("g_send_publish_prompt", labels={"chat_id": chat_id}):
            self.telegram.send_message(chat_id, get_message("publish_prompt"), reply_markup=keyboard)
        if total_memory is not None:
            with Timer("g_send_memory_added", labels={"chat_id": chat_id}):
                self.telegram.send_message(chat_id, get_message("memory_added", total=total_memory))
        with Timer("g_send_ready_for_next", labels={"chat_id": chat_id}):
            self.telegram.send_message(chat_id, get_message("ready_for_next"), reply_markup=self.telegram.get_new_tweet_keyboard())
        with Timer("g_delete_temp_draft", labels={"chat_id": chat_id}):
            self.drafts.delete(chat_id, topic_id)
