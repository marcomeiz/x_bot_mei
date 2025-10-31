import os
import time
from openai import OpenAI
import threading
import chromadb
from chromadb.config import Settings as ChromaSettings
from dotenv import load_dotenv
from typing import List, Optional

from logger_config import logger
from src.settings import AppSettings
import json as _json
import requests
from urllib.parse import urlparse

load_dotenv()

_chroma_client = None
_chroma_lock = threading.Lock()
_last_embed_error_ts: float = 0.0
# Modelo de embeddings efectivo (override dinámico si el default falla)
_embed_model_override: Optional[str] = None
_embed_fallback_candidates = (
    # Prefer stable providers first
    "jinaai/jina-embeddings-v2-base-en",
    "openai/text-embedding-3-small",
    "thenlper/gte-small",
)

_embed_client: Optional[OpenAI] = None
_embed_client_lock = threading.Lock()

def _get_embed_client() -> OpenAI:
    global _embed_client
    if _embed_client is None:
        with _embed_client_lock:
            if _embed_client is None:
                s = AppSettings.load()
                if not s.openrouter_api_key:
                    logger.warning("OPENROUTER_API_KEY no configurada para embeddings.")
                # OpenAI SDK compatible con OpenRouter
                _embed_client = OpenAI(base_url=s.openrouter_base_url, api_key=s.openrouter_api_key)
    return _embed_client

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
                    # Parse host/port/ssl
                    raw = chroma_url.strip()
                    parsed = urlparse(raw if "://" in raw else f"http://{raw}")
                    host = parsed.hostname or raw
                    port = parsed.port
                    use_ssl = (parsed.scheme == "https")
                    log_target = f"{host}:{port}" if port else host
                    # Intento API v2 (HttpClient)
                    try:
                        logger.info(f"Inicializando cliente HTTP de ChromaDB (host='{log_target}', ssl={use_ssl})…")
                        _chroma_client = chromadb.HttpClient(host=host, port=port, ssl=use_ssl)
                        # llamada ligera para forzar handshake y versión
                        try:
                            _ = _chroma_client.get_version()  # v2 debería exponer versión
                        except Exception:
                            pass
                    except Exception as e_v2:
                        logger.warning(f"Chroma v2 HttpClient falló ({e_v2}). Probando modo legacy (v1)…")
                        # Fallback API v1 (legacy REST)
                        try:
                            settings = ChromaSettings(
                                chroma_api_impl="rest",
                                chroma_server_host=host,
                                chroma_server_http_port=port or 80,
                                chroma_server_ssl_enabled=use_ssl,
                            )
                            _chroma_client = chromadb.Client(settings)
                            # Verificar que responde
                            try:
                                _ = _chroma_client.heartbeat()
                            except Exception:
                                pass
                            logger.info("Cliente Chroma legacy (v1) inicializado correctamente.")
                        except Exception as e_v1:
                            logger.critical(
                                f"Fallo conectando con Chroma HTTP (v2) y legacy (v1). v2='{e_v2}' v1='{e_v1}'",
                                exc_info=True,
                            )
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
    """Genera el embedding para un texto dado usando el SDK OpenAI contra OpenRouter."""
    global _last_embed_error_ts, _embed_model_override
    # Circuit breaker: evita timeouts repetidos durante 60s tras fallo (se puede desactivar por env)
    circuit_disabled = os.getenv("EMBED_DISABLE_CIRCUIT", "0").lower() in {"1", "true", "yes"}
    if (not circuit_disabled) and _last_embed_error_ts and (time.time() - _last_embed_error_ts) < 60:
        logger.warning("Embedding circuit open (recent failures); skipping embedding generation.")
        return None
    s = AppSettings.load()
    model_name = _embed_model_override or s.embed_model
    logger.info(f"Generando embedding (model={model_name}) para: '{text[:50]}...'")

    def _sdk_call(model: str) -> Optional[list]:
        try:
            client = _get_embed_client()
            # Usar llamada directa; algunas versiones no soportan with_options en embeddings
            resp = client.embeddings.create(model=model, input=[text], timeout=15)
            data = getattr(resp, "data", None) or []
            if not data:
                logger.error("Embedding SDK response missing vector payload")
                return None
            vec = getattr(data[0], "embedding", None)
            if not isinstance(vec, list) or not all(isinstance(x, (int, float)) for x in vec):
                logger.error("Embedding SDK vector invalid type")
                return None
            return vec
        except Exception as e:
            logger.error(f"Embedding SDK error for model '{model}': {e}")
            return None

    def _http_call(model: str) -> Optional[list]:
        try:
            s2 = AppSettings.load()
            url = s2.openrouter_base_url.rstrip('/') + '/embeddings'
            headers = {"Authorization": f"Bearer {s2.openrouter_api_key}", "Content-Type": "application/json"}
            payload = {"model": model, "input": [text]}
            resp = requests.post(url, headers=headers, data=_json.dumps(payload), timeout=10)
            raw = resp.text
            try:
                data = resp.json()
            except Exception:
                try:
                    data = _json.loads(raw)
                except Exception:
                    data = {}
            if resp.status_code != 200:
                snippet = raw[:240].replace("\n", " ")
                logger.error(f"Embeddings HTTP error {resp.status_code}: {snippet}")
                return None
            if isinstance(data, dict) and "error" in data:
                snippet = str(data.get("error"))[:240]
                logger.error(f"Embeddings HTTP returned error payload: {snippet}")
                return None
            arr = (data.get("data") if isinstance(data, dict) else None)
            vec = None
            if isinstance(arr, list) and arr:
                vec = arr[0].get("embedding") if isinstance(arr[0], dict) else None
            # Tolerancia a esquemas alternativos
            if vec is None and isinstance(data, dict):
                # Algunos proveedores devuelven {"embedding": [...]} directo
                maybe_vec = data.get("embedding")
                if isinstance(maybe_vec, list):
                    vec = maybe_vec
            if not isinstance(vec, list) or not all(isinstance(x, (int, float)) for x in vec):
                logger.error("Embedding HTTP vector invalid/missing in response")
                return None
            if not isinstance(vec, list) or not all(isinstance(x, (int, float)) for x in vec):
                logger.error("Embedding HTTP vector invalid type")
                return None
            return vec
        except Exception as ee:
            logger.error(f"Embedding HTTP exception: {ee}")
            return None

    # Intento HTTP-first para mayor compatibilidad; luego SDK
    vec = _http_call(model_name) or _sdk_call(model_name)
    if vec is not None:
        return vec

    # Probar candidatos alternativos baratos soportados en OR
    for candidate in _embed_fallback_candidates:
        if candidate == model_name:
            continue
        logger.warning(f"Embedding fallback: probando modelo alternativo '{candidate}'")
        vec2 = _http_call(candidate) or _sdk_call(candidate)
        if vec2 is not None:
            _embed_model_override = candidate
            logger.info(f"Embedding model conmutado dinámicamente a '{candidate}'")
            return vec2

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
