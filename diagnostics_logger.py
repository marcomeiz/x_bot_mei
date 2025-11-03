import json
import os
from typing import Dict, Any, Optional

from logger_config import logger
from src.goldset import GOLDSET_MIN_SIMILARITY, max_similarity_to_goldset
from src.style_config import get_style_settings


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)) or default)
    except Exception:
        return default


def get_thresholds() -> Dict[str, Any]:
    """Collects all relevant thresholds for comparison in logs."""
    style = get_style_settings()
    return {
        "variant_similarity_threshold": _env_float("VARIANT_SIMILARITY_THRESHOLD", 0.78),
        "goldset_min_similarity": float(GOLDSET_MIN_SIMILARITY),
        "style_hedging_threshold": style.get("hedging_threshold", 1),
        "style_jargon_block_threshold": style.get("jargon_block_threshold", 1),
        "style_memory_similarity_floor": style.get("memory_similarity_floor", 0.35),
        "eval_confidence_threshold": _env_float("EVAL_CONFIDENCE_THRESHOLD", 4.5),
        "length_limit_chars": 280,
        "enforce_no_commas": os.getenv("ENFORCE_NO_COMMAS", "1").lower() in {"1", "true", "yes"},
    }


def compute_basic_metrics(text: str) -> Dict[str, Any]:
    """Computes simple metrics directly from the text."""
    t = (text or "").strip()
    words = [w for w in t.split() if w]
    speaks_to_you = bool(
        " you " in f" {t.lower()} "
        or " your " in f" {t.lower()} "
        or " you're " in f" {t.lower()} "
        or " you'll " in f" {t.lower()} "
    )
    return {
        "chars": len(t),
        "words": len(words),
        "has_commas": "," in t,
        "speaks_to_you": speaks_to_you,
        "goldset_similarity": max_similarity_to_goldset(t),
    }


def log_post_metrics(
    *,
    chat_id: int,
    topic: Dict[str, Any],
    drafts: Dict[str, Optional[str]],
    evaluations: Dict[str, Dict[str, Any]],
    pair_similarity: Optional[Dict[str, Any]] = None,
    blocked: bool = False,
    blocking_reason: Optional[str] = None,
) -> None:
    """Logs a structured JSON entry with all metrics and thresholds per variant.

    - drafts: mapping label -> text
    - evaluations: mapping label -> evaluation payloads (fast/slow)
    - pair_similarity: optional dict with {'labels': 'A-B', 'similarity': float}
    """
    thresholds = get_thresholds()
    topic_id = topic.get("id") or topic.get("topic_id")
    abstract = topic.get("abstract") or ""
    title = topic.get("title") or topic_id

    per_variant: Dict[str, Any] = {}
    for label, text in drafts.items():
        base = compute_basic_metrics(text or "")
        eval_payload = evaluations.get(label) or {}
        fast_eval = eval_payload.get("fast_eval") or {}
        slow_eval = eval_payload.get("slow_eval") or {}
        per_variant[label] = {
            "text": text or "",
            "metrics": base,
            "evaluation": {
                "fast": fast_eval,
                "slow": slow_eval,
                "final_score": eval_payload.get("final_score"),
                "slow_eval_skipped": eval_payload.get("slow_eval_skipped", False),
            },
            "thresholds": {
                "goldset_min_similarity": thresholds["goldset_min_similarity"],
                "length_limit_chars": thresholds["length_limit_chars"],
                "enforce_no_commas": thresholds["enforce_no_commas"],
            },
        }

    entry = {
        "event": "EVAL_FAILURE" if blocked else "EVAL_METRICS",
        "timestamp": int(__import__("time").time()),
        "chat_id": chat_id,
        "topic": {
            "id": topic_id,
            "title": title,
            "abstract": abstract,
        },
        "variants": per_variant,
        "pair_similarity": pair_similarity,
        "global_thresholds": thresholds,
        "blocked": blocked,
        "blocking_reason": blocking_reason,
    }

    try:
        logger.info(json.dumps(entry, ensure_ascii=False))
    except Exception:
        # Fallback to plain log if JSON formatting fails
        logger.info("[DIAG] %s", entry)

