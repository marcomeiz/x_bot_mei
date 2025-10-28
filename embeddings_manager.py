import os
from openai import OpenAI
import threading
import chromadb
from dotenv import load_dotenv
from typing import List

from logger_config import logger
from src.settings import AppSettings

load_dotenv()

_chroma_client = None
_chroma_lock = threading.Lock()

def get_chroma_client():
    """
    Devuelve una instancia única del cliente de ChromaDB.
    Prioriza la conexión HTTP si CHROMA_DB_URL está definida; de lo contrario,
    usa un cliente persistente local.
    """
    global _chroma_client
    if _chroma_client is None:
        with _chroma_lock:
            if _chroma_client is None:
                chroma_url = os.getenv("CHROMA_DB_URL")
                # Si CHROMA_DB_URL está presente, SIEMPRE usamos el cliente HTTP
                if chroma_url:
                    try:
                        logger.info(f"Inicializando cliente HTTP de ChromaDB (url='{chroma_url}')...")
                        # El cliente infiere el puerto y el protocolo desde la URL
                        _chroma_client = chromadb.HttpClient(host=chroma_url)
                    except Exception as e:
                        logger.critical(f"Fallo crítico conectando con el servidor ChromaDB: {e}", exc_info=True)
                        raise
                else:
                    # Entorno local sin URL definida
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
    """Genera el embedding para un texto dado usando OpenRouter (OpenAI-compatible embeddings)."""
    try:
        s = AppSettings.load()
        logger.info(f"Generando embedding (model={s.embed_model}) para: '{text[:50]}...'")
        client = OpenAI(base_url=s.openrouter_base_url, api_key=s.openrouter_api_key)
        resp = client.embeddings.create(model=s.embed_model, input=text)
        emb = resp.data[0].embedding if getattr(resp, 'data', None) else None
        return emb
    except Exception as e:
        logger.error(f"Error al generar embedding vía OpenRouter: {e}", exc_info=True)
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
