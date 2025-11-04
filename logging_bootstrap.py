import logging
import datetime

try:
    import google.cloud.logging
    from google.cloud.logging_v2.handlers import StructuredLogHandler

    client = google.cloud.logging.Client()
    client.setup_logging()  # engancha StructuredLogHandler al root

    # Señal de vida en jsonPayload (boot)
    logging.getLogger(__name__).info({
        "message": "DIAG_STRUCTURED_OK",
        "ts": datetime.datetime.utcnow().isoformat() + "Z",
    })
except Exception as e:
    # Fallback sólo si falla setup_logging
    logging.basicConfig(level=logging.INFO)
    logging.getLogger(__name__).warning(f"Cloud logging fallback: {e}")
