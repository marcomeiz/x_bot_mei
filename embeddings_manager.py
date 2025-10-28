import os
import time
from openai import OpenAI
import threading
import chromadb
from dotenv import load_dotenv
from typing import List, Optional

from logger_config import logger
from src.settings import AppSettings
import requests

load_dotenv()

_chroma_client = None
_chroma_lock = threading.Lock()
_last_embed_error_ts: float = 0.0
# Modelo de embeddings efectivo (override dinámico si el default falla)
_embed_model_override: Optional[str] = None
_embed_fallback_candidates = (
    "openai/text-embedding-3-small",
    "thenlper/gte-small",
    "jinaai/jina-embeddings-v2-base-en",
)

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
    global _last_embed_error_ts, _embed_model_override
    # Circuit breaker: if there was an error in the last 60s, skip to avoid long timeouts
    if _last_embed_error_ts and (time.time() - _last_embed_error_ts) < 60:
        logger.warning("Embedding circuit open (recent failures); skipping embedding generation.")
        return None
    try:
        s = AppSettings.load()
        model_name = _embed_model_override or s.embed_model
        logger.info(f"Generando embedding (model={model_name}) para: '{text[:50]}...'")
        # Prefer HTTP path (more predictable in this environment)
        url = s.openrouter_base_url.rstrip('/') + '/embeddings'
        headers = {
            'Authorization': f'Bearer {s.openrouter_api_key}',
            'Content-Type': 'application/json'
        }
        
        def _http_call(model: str) -> Optional[list]:
            payload = {"model": model, "input": text}
            resp = requests.post(url, headers=headers, json=payload, timeout=6)
            ctype = resp.headers.get('content-type', '')
            data = resp.json() if 'application/json' in ctype else {}
            if resp.status_code != 200:
                logger.error(f"Embeddings HTTP error {resp.status_code}: {str(data)[:200]}")
                return None
            arr = data.get('data') or []
            if not arr or not isinstance(arr, list):
                logger.error("Embedding HTTP response missing data array")
                return None
            vec = arr[0].get('embedding')
            if not isinstance(vec, list) or not all(isinstance(x, (int, float)) for x in vec):
                logger.error("Embedding HTTP vector invalid type")
                return None
            return vec

        # Intento con el modelo actual
        vec = _http_call(model_name)
        if vec is not None:
            return vec

        # Probar candidatos alternativos baratos soportados en OR
        for candidate in _embed_fallback_candidates:
            if candidate == model_name:
                continue
            logger.warning(f"Embedding fallback: probando modelo alternativo '{candidate}'")
            vec2 = _http_call(candidate)
            if vec2 is not None:
                _embed_model_override = candidate
                logger.info(f"Embedding model conmutado dinámicamente a '{candidate}'")
                return vec2

        _last_embed_error_ts = time.time()
        return None
    except Exception as ee:
        logger.error(f"Error inesperado generando embedding: {ee}", exc_info=True)
        _last_embed_error_ts = time.time()
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
