import os
import google.generativeai as genai
import threading
import chromadb
from dotenv import load_dotenv
from typing import List

# --- NUEVO: Importar el logger configurado ---
from logger_config import logger

load_dotenv()

# La configuración de la API puede ser global
try:
    genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
except Exception as e:
    # --- MODIFICADO: Usar el logger para registrar el error ---
    logger.critical(f"No se pudo configurar la API de Google. El programa no puede continuar. Error: {e}", exc_info=True)
    # Considera salir del programa si esta configuración es crítica
    # exit(1)

# Patrón para asegurar una única instancia del cliente por proceso
_chroma_client = None
_chroma_lock = threading.Lock()

def get_chroma_client():
    """
    Devuelve una instancia única del cliente de ChromaDB, seleccionando el tipo de cliente
    basado en el entorno de ejecución (Cloud Run vs. local).
    """
    global _chroma_client
    if _chroma_client is None:
        with _chroma_lock:
            if _chroma_client is None:
                # Si K_SERVICE está presente, estamos en Cloud Run
                if os.getenv('K_SERVICE'):
                    chroma_url = os.getenv("CHROMA_DB_URL")
                    if not chroma_url:
                        logger.critical("Error: Ejecutando en producción pero CHROMA_DB_URL no está configurada.")
                        raise ValueError("CHROMA_DB_URL must be set in production environment")
                    try:
                        logger.info(f"Inicializando cliente HTTP de ChromaDB para producción (url='{chroma_url}')...")
                        # El puerto es manejado por Cloud Run, solo necesitamos el host base
                        _chroma_client = chromadb.HttpClient(host=chroma_url, port=80) 
                    except Exception as e:
                        logger.critical(f"Fallo crítico conectando con el servidor ChromaDB de producción: {e}", exc_info=True)
                        raise
                else:
                    # Entorno local
                    try:
                        db_path = os.getenv("CHROMA_DB_PATH", "db")
                        logger.info(f"Inicializando cliente persistente local de ChromaDB (path='{db_path}')...")
                        os.makedirs(db_path, exist_ok=True)
                        _chroma_client = chromadb.PersistentClient(path=db_path)
                    except Exception as e:
                        logger.critical(f"Fallo crítico creando cliente ChromaDB local: {e}", exc_info=True)
                        raise
    return _chroma_client

def get_topics_collection():
    """Obtiene la colección de temas."""
    client = get_chroma_client()
    return client.get_or_create_collection(name="topics_collection", metadata={"hnsw:space": "cosine"})

def get_memory_collection():
    """Obtiene la colección de memoria."""
    client = get_chroma_client()
    return client.get_or_create_collection(name="memory_collection", metadata={"hnsw:space": "cosine"})

def get_embedding(text: str):
    """Genera el embedding para un texto dado."""
    try:
        # --- NUEVO: Registrar la llamada a la API ---
        logger.info(f"Generando embedding para texto: '{text[:50]}...'")
        result = genai.embed_content(model="models/embedding-001", content=text, task_type="RETRIEVAL_DOCUMENT")
        return result['embedding']
    except Exception as e:
        # --- NUEVO: Registrar el error específico de la API ---
        logger.error(f"Error al llamar a la API de Google para generar embedding: {e}", exc_info=True)
        return None

def find_similar_topics(topic_text: str, n_results: int = 4) -> List[str]:
    """Finds similar topics in the topics_collection."""
    logger.info(f"Buscando temas similares a: '{topic_text[:50]}...'")
    topic_embedding = get_embedding(topic_text)
    if not topic_embedding:
        logger.warning("No se pudo generar el embedding para el tema, no se puede buscar similitud.")
        return []

    try:
        topics_collection = get_topics_collection()
        results = topics_collection.query(
            query_embeddings=[topic_embedding],
            n_results=n_results
        )
        
        # Exclude the exact match which is likely the topic itself
        similar_docs = [doc for doc in results['documents'][0] if doc != topic_text]
        
        logger.info(f"Se encontraron {len(similar_docs)} temas similares.")
        return similar_docs
    except Exception as e:
        logger.error(f"Error al buscar temas similares en ChromaDB: {e}", exc_info=True)
        return []
