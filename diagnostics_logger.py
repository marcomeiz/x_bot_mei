import logging
from typing import Dict, Any, Optional


def emit_structured(payload: Dict[str, Any]) -> None:
    """Emite un dict como jsonPayload vía logging (diagnostics logger)."""
    logging.getLogger("diagnostics").info(payload)


def log_post_metrics(
    piece_id: Optional[str],
    variant: str,
    draft_text: Optional[str],
    similarity: Optional[float],
    min_required: float,
    passed: bool,
    **extra,
) -> None:
    """Shim de compatibilidad para código existente.
    Construye el payload esperado y lo envía como jsonPayload.
    """
    p = {
        "event": "variant_evaluation",
        "piece_id": piece_id,
        "variant": variant,
        "draft_text": draft_text,
        "similarity": similarity,
        "min_required": min_required,
        "passed": passed,
        "emb_model_runtime": extra.get("emb_model_runtime"),
        "emb_model_goldset": extra.get("emb_model_goldset"),
        "sim_kind": extra.get("sim_kind"),
        "goldset_collection": extra.get("goldset_collection"),
        "timestamp": extra.get("timestamp"),
    }
    emit_structured({k: v for k, v in p.items() if v is not None})
