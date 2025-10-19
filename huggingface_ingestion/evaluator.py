import os
from dataclasses import dataclass
from typing import Dict, Optional

from llm_fallback import llm
from logger_config import logger
from prompt_context import PromptContext


@dataclass
class EvaluationResult:
    answers: Dict[str, Dict[str, str]]
    all_passed: bool
    raw_response: Dict


def _truncate(text: str, max_chars: int) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def evaluate_signal(
    text: str,
    source_name: str,
    context: PromptContext,
    max_chars: int = 1600,
) -> EvaluationResult:
    """Run the strict evaluation flow for a raw signal."""

    truncated = _truncate(text, max_chars)
    system_prompt = (
        "You are an unforgiving evaluator vetting market signals for a digital solopreneur ICP. "
        "Answer in strict JSON. For each question, respond with an object {\"answer\": true/false, \"reason\": str<=120}. "
        "Reject anything generic, fluffy, or unrelated to digital products, cohorts, or subscription businesses."
    )
    user_prompt = (
        "ICP summary:\n"
        f"{context.icp}\n\n"
        "Signal to evaluate:\n"
        f"{truncated}\n\n"
        "Answer the following questions:\n"
        "1. icp_fit — Does this describe a real operational pain for a day-1 to year-1 digital solopreneur (courses, cohorts, communities, productized services)?\n"
        "2. actionable — Can the COOlogy toolkit help with a concrete, step-zero action (system, template, automation, delegable process)?\n"
        "3. stage_context — Is it clear in what moment of the business (pre-launch, delivery, retention, cashflow) this pain happens?\n"
        "4. urgency — Does the language show urgency/bleeding (missed revenue, churn risk, burnout) instead of generic advice?\n\n"
        "Respond ONLY with JSON in the shape {\"answers\": {\"icp_fit\": {\"answer\": bool, \"reason\": str}, ...}}."
    )
    try:
        raw = llm.chat_json(
            model=os.getenv("GEMINI_MODEL", "gemini-2.5-pro"),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,
        )
    except Exception as exc:
        logger.error("Evaluación LLM falló para %s: %s", source_name, exc, exc_info=True)
        return EvaluationResult(answers={}, all_passed=False, raw_response={})

    if not isinstance(raw, dict):
        logger.warning("Respuesta de evaluación sin formato JSON. Rechazado.")
        return EvaluationResult(answers={}, all_passed=False, raw_response={})

    answers = raw.get("answers") if isinstance(raw.get("answers"), dict) else {}
    normalised: Dict[str, Dict[str, str]] = {}
    all_passed = True
    for key in ("icp_fit", "actionable", "stage_context", "urgency"):
        entry = answers.get(key)
        if not isinstance(entry, dict):
            all_passed = False
            continue
        answer = entry.get("answer")
        reason = entry.get("reason", "")
        is_true = bool(answer) if isinstance(answer, bool) else False
        if not is_true:
            all_passed = False
        normalised[key] = {
            "answer": is_true,
            "reason": str(reason)[:160],
        }

    return EvaluationResult(answers=normalised, all_passed=all_passed, raw_response=raw)


def craft_topic_from_signal(
    text: str,
    context: PromptContext,
    extra_tags: Optional[list] = None,
    max_chars: int = 1600,
) -> Optional[Dict]:
    """Condense a vetted signal into a single-topic payload."""

    truncated = _truncate(text, max_chars)
    tag_str = ", ".join(extra_tags or [])
    system_prompt = (
        "You transform raw signals into sharp, tweet-ready prompts for the COOlogy Ledger. "
        "Stay within 200 characters for the final topic. "
        "You must respect the voice contract and ICP."
    )
    user_prompt = (
        f"Style contract:\n{context.contract}\n\n"
        f"Final review guardrails:\n{context.final_guidelines}\n\n"
        f"ICP:\n{context.icp}\n\n"
        f"Raw signal:\n{truncated}\n\n"
        "Produce a JSON object with:\n"
        "- topic: a single sentence (<=200 chars) stating the pain and promise for a digital solopreneur.\n"
        "- pain_point: max 120 chars explaining what hurts.\n"
        "- leverage: max 120 chars on what tactical move we can propose (system, automation, template).\n"
        "- tags: array of slugs (include the ones provided if relevant).\n"
    )
    if tag_str:
        user_prompt += f"\nPreferred tags: {tag_str}\n"
    try:
        raw = llm.chat_json(
            model=os.getenv("GEMINI_MODEL", "gemini-2.5-pro"),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
        )
    except Exception as exc:
        logger.error("No se pudo sintetizar tópico desde señal: %s", exc, exc_info=True)
        return None

    if not isinstance(raw, dict):
        logger.warning("Respuesta de síntesis inválida; se descarta señal.")
        return None

    topic = str(raw.get("topic", "")).strip()
    if not topic or len(topic) > 240:
        logger.warning("Tópico sintetizado inválido o demasiado largo. Se descarta.")
        return None

    payload = {
        "topic": topic,
        "pain_point": str(raw.get("pain_point", "")).strip()[:160],
        "leverage": str(raw.get("leverage", "")).strip()[:160],
        "tags": list(raw.get("tags") or []),
    }
    if extra_tags:
        existing = set(payload["tags"])
        for tag in extra_tags:
            if tag not in existing:
                payload["tags"].append(tag)
    return payload
