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
    root_logger.setLevel(logging.INFO)
    if not any(isinstance(h, StructuredLogHandler) for h in root_logger.handlers):
        root_logger.addHandler(_structured_handler)
except Exception:
    _diag_logger = logging.getLogger("diagnostics_fallback")
    _diag_logger.setLevel(logging.INFO)


def emit_structured(payload: Dict[str, Any]) -> None:
    """Emit structured logging payload (jsonPayload in Cloud Logging)."""
    try:
        _diag_logger.info(payload)
    except Exception:
        pass
