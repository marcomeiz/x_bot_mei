import logging
from typing import Dict, Any

try:
    from google.cloud.logging_v2.handlers import StructuredLogHandler

    _structured_handler = StructuredLogHandler()
    _diag_logger = logging.getLogger("diagnostics")
    _diag_logger.setLevel(logging.INFO)
    if not any(isinstance(h, StructuredLogHandler) for h in _diag_logger.handlers):
        _diag_logger.addHandler(_structured_handler)

    root_logger = logging.getLogger()
    # Limpia handlers previos para evitar formateadores o handlers que añadan
    # prefijos/texto no JSON y asegurar que Cloud Logging reciba jsonPayload.
    root_logger.handlers = []  # <<–– añade esto para limpiar interferencias
    root_logger.addHandler(_structured_handler)
    root_logger.setLevel(logging.INFO)
except Exception:
    _diag_logger = logging.getLogger("diagnostics_fallback")
    _diag_logger.setLevel(logging.INFO)


def emit_structured(payload: Dict[str, Any]) -> None:
    """Emit structured logging payload (jsonPayload in Cloud Logging)."""
    try:
        _diag_logger.info(payload)
    except Exception:
        pass

# Señal de arranque para diagnósticos en logs
try:
    emit_structured({"message": "DIAG_STRUCTURED_OK"})
except Exception:
    pass

# --- Shim de compatibilidad: mantener al final del archivo ---
from typing import Optional

def log_post_metrics(
    piece_id: Optional[str],
    variant: str,
    draft_text: Optional[str],
    similarity: float,
    min_required: float,
    passed: bool,
    **extra,
) -> None:
    """
    Shim de compatibilidad para módulos que aún importan `log_post_metrics`.
    Internamente reusa `emit_structured` con el esquema nuevo.
    """
    payload = {
        "event": "variant_evaluation",
        "piece_id": piece_id,
        "variant": variant,
        "draft_text": draft_text,
        "similarity": similarity,
        "min_required": min_required,
        "passed": passed,
        # metadatos útiles si vienen
        "emb_model_runtime": extra.get("emb_model_runtime"),
        "emb_model_goldset": extra.get("emb_model_goldset"),
        "sim_kind": extra.get("sim_kind"),
        "goldset_collection": extra.get("goldset_collection"),
        "timestamp": extra.get("timestamp"),
    }
    # limpia claves None para no ensuciar jsonPayload
    payload = {k: v for k, v in payload.items() if v is not None}
    emit_structured(payload)
