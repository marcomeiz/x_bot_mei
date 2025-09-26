# logger_config.py
import logging
import sys

def setup_logger():
    """Configura un logger centralizado."""
    # Evita añadir múltiples handlers si la función se llama más de una vez
    if logging.getLogger('bot_logger').hasHandlers():
        return logging.getLogger('bot_logger')

    logger = logging.getLogger('bot_logger')
    logger.setLevel(logging.INFO)  # Nivel de log: INFO, WARNING, ERROR, CRITICAL

    # Formato del log
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Handler para enviar los logs a la consola
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)

    logger.addHandler(stream_handler)
    return logger

# Crea una instancia del logger para ser importada por otros módulos
logger = setup_logger()