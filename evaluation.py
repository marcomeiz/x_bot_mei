import os
from typing import Dict, Optional

from llm_fallback import llm
from logger_config import logger
from prompt_context import PromptContext


DEFAULT_EVAL_MODEL = os.getenv("EVAL_MODEL") or os.getenv("GEMINI_MODEL", "gemini-2.5-pro")


def evaluate_draft(text: str, context: PromptContext) -> Optional[Dict[str, object]]:
    """Return a lightweight evaluation of a draft (style & factual cues)."""
    if not text:
        return None

    prompt = (
        "You are reviewing a tweet draft destined to a NYC-style COO persona. Evaluate it using the rubric below.\n\n"
        "Rubric (score 1-5, 5 best):\n"
        "1. Style Sharpness – voz directa, ritmo ágil, cero hedging/corporativismo.\n"
        "2. Contrarian Signal – ¿abre con un ángulo incómodo, no obvio, que desafía la narrativa mainstream?\n"
        "3. Clarity & Specificity – ¿usa ejemplos, métricas o imágenes concretas? Evita abstracciones vacías.\n"
        "4. Factual Soundness – ¿hay datos dudosos? Marca factuality = risky si detectas afirmaciones peligrosas.\n\n"
        "Responde SOLO con JSON en el formato:\n"
        "{\n"
        '  "style_score": int (1..5, 5 best),\n'
        '  "factuality": "ok"|"unclear"|"risky",\n'
        '  "needs_revision": boolean,\n'
        '  "summary": string (<=120 chars, sin saltos de línea),\n'
        '  "analysis": [\n'
        '     {"dimension": "style", "score": int, "comment": string<=160},\n'
        '     {"dimension": "contrarian", "score": int, "comment": string<=160},\n'
        '     {"dimension": "clarity", "score": int, "comment": string<=160},\n'
        '     {"dimension": "factuality", "comment": string<=160}\n'
        "  ]\n"
        "}\n\n"
        "Primero razona brevemente dentro de los comentarios antes de dar el veredicto. Marca needs_revision=true si algún criterio cae ≤2 ó si factuality es risky."
    )

    try:
        payload = llm.chat_json(
            model=DEFAULT_EVAL_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You evaluate drafts. Be concise, deterministic. Respond ONLY with strict JSON.\n\n"
                        "<STYLE_CONTRACT>\n"
                        + context.contract
                        + "\n</STYLE_CONTRACT>\n\n"
                        "<ICP>\n"
                        + context.icp
                        + "\n</ICP>\n\n"
                        "<FINAL_REVIEW_GUIDELINES>\n"
                        + context.final_guidelines
                        + "\n</FINAL_REVIEW_GUIDELINES>"
                    ),
                },
                {"role": "user", "content": f"{prompt}\n\nDRAFT:\n{text}"},
            ],
            temperature=0.2,
        )
        if isinstance(payload, dict):
            summary = str(payload.get("summary", "")).strip().replace("\n", " ")
            payload["summary"] = summary[:120]

            # Normalize scores/flags
            try:
                payload["style_score"] = int(payload.get("style_score", 0))
            except Exception:
                payload["style_score"] = 0
            payload["needs_revision"] = bool(payload.get("needs_revision", False))

            analysis = payload.get("analysis")
            cleaned_analysis: list[Dict[str, object]] = []
            if isinstance(analysis, list):
                for item in analysis:
                    if not isinstance(item, dict):
                        continue
                    comment = str(item.get("comment", "")).strip().replace("\n", " ")
                    if comment:
                        item["comment"] = comment[:160]
                    else:
                        item.pop("comment", None)
                    if "score" in item:
                        try:
                            item["score"] = int(item["score"])
                        except Exception:
                            item.pop("score", None)
                    dimension = str(item.get("dimension", "")).strip()
                    if dimension:
                        item["dimension"] = dimension
                    cleaned_analysis.append(item)
            payload["analysis"] = cleaned_analysis

            return payload
    except Exception as exc:
        logger.warning("Draft evaluation failed: %s", exc)
    return None
