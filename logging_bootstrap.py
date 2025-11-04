import logging

try:
    import google.cloud.logging
    from google.cloud.logging_v2.handlers import StructuredLogHandler

    client = google.cloud.logging.Client()
    client.setup_logging()  # <-- clave mágica (añade StructuredLogHandler al root)

except Exception as e:
    logging.basicConfig(level=logging.INFO)
    logging.getLogger(__name__).warning(f"Cloud logging fallback: {e}")

