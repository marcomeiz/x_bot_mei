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
        "You are reviewing a tweet draft destined to a NYC-style COO persona. "
        "Return a JSON object with:\n"
        "{\n"
        '  "style_score": int (1..5, 5 best),\n'
        '  "factuality": "ok"|"unclear"|"risky",\n'
        '  "needs_revision": boolean,\n'
        '  "summary": string (concise, <=120 chars, human readable)\n'
        "}\n"
        "Flag needs_revision if the tone drifts from the style contract, if the draft hedges, sounds corporate, or makes dubious claims. "
        "If factuality = risky, explain briefly in summary. Never include newlines in summary."
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
            summary = str(payload.get("summary", "")).strip()
            payload["summary"] = summary[:120]
            return payload
    except Exception as exc:
        logger.warning("Draft evaluation failed: %s", exc)
    return None

