
import asyncio
import os
from dotenv import load_dotenv
from watcher_v2 import watch_directory
from logger_config import logger
import embeddings_manager
import chromadb

def main():
    # Carga las variables de entorno específicas para producción
    load_dotenv(dotenv_path='.env.prod', override=True)
    logger.info("Cargadas variables de entorno de '.env.prod' para la ingestión.")

    chroma_url = os.getenv("CHROMA_DB_URL")
    if not chroma_url:
        logger.critical("Error: CHROMA_DB_URL no está definida en .env.prod. No se puede continuar.")
        return

    # Forzamos el uso del cliente HTTP para este script
    logger.info(f"Forzando el uso del cliente HTTP para producción: {chroma_url}")
    embeddings_manager._chroma_client = chromadb.HttpClient(host=chroma_url)

    base_dir = os.path.dirname(__file__)
    uploads_dir = os.path.abspath(os.path.join(base_dir, 'uploads'))
    processed_dir = os.path.abspath(os.path.join(base_dir, 'processed_pdfs'))

    # Vaciar la carpeta de procesados antes de empezar
    if os.path.exists(processed_dir):
        for f in os.listdir(processed_dir):
            os.remove(os.path.join(processed_dir, f))
        logger.info(f"Directorio '{processed_dir}' limpiado.")

    if not os.listdir(uploads_dir):
        logger.warning("El directorio 'uploads' está vacío. No hay nada que ingerir.")
        return

    logger.info("Iniciando el proceso de ingestión para producción...")
    try:
        # Ejecutamos el watcher una sola vez, no en un bucle infinito
        # El watcher ahora usará el cliente HTTP que hemos inyectado.
        asyncio.run(watch_directory(uploads_dir, processed_dir))
        logger.info("Proceso de ingestión completado.")
    except KeyboardInterrupt:
        logger.info("Ingestión interrumpida por el usuario.")
    except Exception as e:
        logger.critical(f"Ha ocurrido un error crítico durante la ingestión: {e}", exc_info=True)

if __name__ == "__main__":
    main()
