import os
import re
import threading
import time
from itertools import combinations
from typing import Callable, Dict, Optional, Tuple
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
from llm_fallback import llm
from src.messages import get_message
from src.goldset import GOLDSET_MIN_SIMILARITY, max_similarity_to_goldset
from src.settings import AppSettings


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
                get_message("no_topics_available"),
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
            reason = extracted_reason or getattr(skip_reason, "message", "") or get_message("comment_skip_default_reason")
            logger.info("[CHAT_ID: %s] Comentario omitido: %s", chat_id, reason)
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
                    self.telegram.send_message(chat_id, get_message("unexpected_error", error=e2))
                    return False
            else:
                logger.error(f"[CHAT_ID: {chat_id}] Error generating tweet from topic: {e}", exc_info=True)
                self.telegram.send_message(chat_id, get_message("unexpected_error", error=e))
                return False

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
            self.telegram.send_message(chat_id, get_message("variants_similar_initial"))
            return False

        compliance_warnings, blocking_contract = self._check_contract_requirements({
            "short": draft_a,
            "mid": draft_b,
            "long": draft_c,
        })
        if blocking_contract:
            if ignore_similarity:
                self.telegram.send_message(
                    chat_id,
                    get_message("contract_failure"),
                    reply_markup=self.telegram.get_new_tweet_keyboard(),
                )
                return False
            self.telegram.send_message(chat_id, get_message("contract_retry"))
            return False
        if compliance_warnings:
            logger.info(
                "[CHAT_ID: %s] Variantes con avisos de contrato: %s",
                chat_id,
                compliance_warnings,
            )
            if LOG_GENERATED_VARIANTS:
                for label, warning in compliance_warnings.items():
                    logger.warning("[CHAT_ID: %s] Draft %s contract check: %s", chat_id, label, warning)
            for key, warning in compliance_warnings.items():
                variant_errors[key] = (
                    f"{warning} " + variant_errors[key]
                ) if key in variant_errors else warning

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

        context = build_prompt_context()
        label_map = {0: "A", 1: "B", 2: "C"}
        draft_map = {"A": draft_a, "B": draft_b, "C": draft_c}
        if LOG_GENERATED_VARIANTS:
            for label, text_variant in draft_map.items():
                logger.info("[CHAT_ID: %s] Draft %s generated (%s chars):\n%s", chat_id, label, len(text_variant or ""), text_variant or "<vacío>")
        evaluations: Dict[str, Dict[str, object]] = {}
        for idx, draft in enumerate([draft_a, draft_b, draft_c]):
            label = label_map[idx]
            if draft:
                raw_eval = evaluate_draft(draft, context)
                evaluations[label] = raw_eval or {}
            else:
                evaluations[label] = {}

        normalized_evals = self._normalize_evaluations(evaluations)
        updated_variants = False
        for label in ("A", "B", "C"):
            eval_data = normalized_evals.get(label)
            text = draft_map.get(label) or ""
            others = {k: v for k, v in draft_map.items() if k != label}
            if self._should_refine_variant(eval_data, text):
                refined = self._refine_variant(label, text, eval_data or {}, topic_abstract or "", context, others)
                if refined and refined != text:
                    draft_map[label] = refined
                    eval_raw = evaluate_draft(refined, context) or {}
                    evaluations[label] = eval_raw
                    normalized_evals[label] = self._normalize_evaluations({label: eval_raw}).get(label, eval_data)
                    updated_variants = True

        if updated_variants:
            draft_a, draft_b, draft_c = draft_map["A"], draft_map["B"], draft_map["C"]
            normalized_evals = self._normalize_evaluations(evaluations)
            similar, pair_info_after = self._check_variant_similarity(draft_map)
            if similar:
                labels = " y ".join(pair_info_after[:2]) if pair_info_after else ""
                sim_value = pair_info_after[2] if pair_info_after else 0.0
                logger.warning(
                    "[CHAT_ID: %s] Variantes aún similares tras reescritura (%s, sim=%.2f). Regenerando…",
                    chat_id,
                    labels,
                    sim_value,
                )
                self.telegram.send_message(chat_id, get_message("variants_similar_after"))
                return False

        compliance_warnings, blocking_contract = self._check_contract_requirements(draft_map)
        if LOG_GENERATED_VARIANTS:
            for label, text in draft_map.items():
                logger.info("[CHAT_ID: %s] Draft %s generated (%s chars):\n%s", chat_id, label, len((text or "")), text or "<vacío>")
            for label, warning in compliance_warnings.items():
                logger.warning("[CHAT_ID: %s] Draft %s contract check: %s", chat_id, label, warning)
        if blocking_contract:
            if ignore_similarity:
                self.telegram.send_message(
                    chat_id,
                    get_message("contract_failure"),
                    reply_markup=self.telegram.get_new_tweet_keyboard(),
                )
                return False
            self.telegram.send_message(chat_id, get_message("contract_retry"))
            return False
        if compliance_warnings:
            logger.info(
                "[CHAT_ID: %s] Variantes con avisos de contrato: %s",
                chat_id,
                compliance_warnings,
            )
            variant_errors.update(compliance_warnings)

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
            evaluations=normalized_evals,
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

    def _check_contract_requirements(self, drafts: Dict[str, str]) -> Tuple[Dict[str, str], bool]:
        warnings: Dict[str, str] = {}
        blocking = False
        spelled_numbers = {
            "one",
            "two",
            "three",
            "four",
            "five",
            "six",
            "seven",
            "eight",
            "nine",
            "ten",
            "eleven",
            "twelve",
            "hundred",
            "thousand",
            "million",
            "billion",
            "percent",
            "quarter",
            "half",
            "double",
            "triple",
        }
        for label, text in drafts.items():
            content = (text or "").strip()
            if not content:
                continue
            lower = content.lower()
            tokens = set(re.findall(r"\b[\w']+\b", lower))
            has_number = bool(re.search(r"\d", content)) or bool(tokens & spelled_numbers) or "k" in tokens or "m" in tokens or "%" in content or "$" in content
            speaks_to_you = bool(
                re.search(r"\byou\b", lower)
                or re.search(r"\byou['’]re\b", lower)
                or re.search(r"\byou['’]ll\b", lower)
                or re.search(r"\byour\b", lower)
            )

            issues = []
            similarity = max_similarity_to_goldset(content)
            if similarity is not None:
                issues.append(f"Sugerencia: refuerza la voz (similitud {similarity:.2f}).")
                if similarity < GOLDSET_MIN_SIMILARITY:
                    blocking = True
            if not has_number:
                issues.append("Sugerencia: añade un número o threshold claro.")
            if not speaks_to_you:
                issues.append("Sugerencia: habla en segunda persona ('you').")
                blocking = True
            if "—" in content:
                issues.append("Sugerencia: reemplaza el em dash (—).")
                blocking = True

            if issues:
                warnings[label] = " ".join(issues)
        return warnings, blocking

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
        self.telegram.send_message(chat_id, get_message("publish_prompt"), reply_markup=keyboard)
        if total_memory is not None:
            self.telegram.send_message(chat_id, get_message("memory_added", total=total_memory))
        self.telegram.send_message(chat_id, get_message("ready_for_next"), reply_markup=self.telegram.get_new_tweet_keyboard())
        self.drafts.delete(chat_id, topic_id)
