import logging
import os
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
    PIPELINE_VERSION = os.getenv("PIPELINE_VERSION", "legacy_v1")
    default_collection = os.getenv("GOLDSET_COLLECTION_NAME", "goldset_norm_v1")
    goldset_collection = extra.get("goldset_collection")
    if not goldset_collection:
        goldset_collection = default_collection
    similarity_value = similarity if similarity is not None else extra.get("similarity")
    similarity_raw = extra.get("similarity_raw")
    similarity_norm = extra.get("similarity_norm")
    max_pair_id = extra.get("max_pair_id")
    p = {
        "event": "variant_evaluation",
        "piece_id": piece_id,
        "variant": variant,
        "draft_text": draft_text,
        "similarity": similarity_value,
        "similarity_raw": similarity_raw,
        "similarity_norm": similarity_norm,
        "max_pair_id": max_pair_id,
        "min_required": min_required,
        "passed": passed,
        "emb_model_runtime": extra.get("emb_model_runtime"),
        "emb_model_goldset": extra.get("emb_model_goldset"),
        "sim_kind": extra.get("sim_kind"),
        "goldset_collection": goldset_collection,
        "timestamp": extra.get("timestamp"),
        # Nuevos campos (back-compat, no rompen consultas actuales)
        "pipeline_version": PIPELINE_VERSION,
        "variant_source": extra.get("variant_source", "gen"),
        "event_stage": extra.get("event_stage"),
    }
    preserve_none = {"similarity", "similarity_raw", "similarity_norm", "max_pair_id", "goldset_collection"}
    payload = {}
    for key, value in p.items():
        if value is not None or key in preserve_none:
            payload[key] = value
    emit_structured(payload)


# --- Nuevo: API mínima de diagnóstico estructurado ---
class Diagnostics:
    """Utilidad para emitir eventos estructurados con distintos niveles.

    Esta clase ayuda a estandarizar la emisión de eventos de diagnóstico
    (info/warn/error) sin romper el contrato existente de emit_structured.
    """

    def info(self, event: str, payload: Dict[str, Any] | None = None) -> None:
        p = {"event": event}
        if payload:
            for k, v in payload.items():
                if k == "event":
                    p["source_event"] = v
                else:
                    p[k] = v
        logging.getLogger("diagnostics").info(p)

    def warn(self, event: str, payload: Dict[str, Any] | None = None) -> None:
        p = {"event": event}
        if payload:
            for k, v in payload.items():
                if k == "event":
                    p["source_event"] = v
                else:
                    p[k] = v
        logging.getLogger("diagnostics").warning(p)

    def error(self, event: str, payload: Dict[str, Any] | None = None) -> None:
        p = {"event": event}
        if payload:
            for k, v in payload.items():
                if k == "event":
                    p["source_event"] = v
                else:
                    p[k] = v
        logging.getLogger("diagnostics").error(p)


# Instancia global para uso sencillo
diagnostics = Diagnostics()
