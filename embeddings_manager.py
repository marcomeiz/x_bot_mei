import os
import time
import hashlib
from pathlib import Path
from collections import OrderedDict
from openai import OpenAI
import threading
import chromadb
from chromadb.config import Settings as ChromaSettings
from dotenv import load_dotenv
from typing import List, Optional, Tuple, Dict

from logger_config import logger
try:
    from metrics import record_metric, Timer
except Exception:
    record_metric = None
    class Timer:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a, **kw): pass
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

# --------- Embeddings Cache (LRU + FS + Chroma) ---------
_embed_cache_lock = threading.Lock()
_embed_cache_lru: OrderedDict[str, List[float]] = OrderedDict()
_embed_cache_capacity = int(os.getenv("EMB_CACHE_CAPACITY", "2048") or 2048)
_emb_fs_cache_enabled = os.getenv("EMB_FS_CACHE", "0").lower() in {"1", "true", "yes"}
_emb_fs_cache_dir = Path(os.getenv("EMB_FS_CACHE_DIR", "data/emb_cache")).resolve()

# --------- GCP Firestore/GCS backend ---------
_emb_firestore_enabled = os.getenv("EMB_USE_FIRESTORE", "1").lower() in {"1", "true", "yes"}
_emb_cache_ttl = int(os.getenv("EMB_CACHE_TTL", "0") or 0)  # segundos; 0 = sin expiración
try:
    from gcp_storage import firestore_get_embedding, firestore_put_embedding  # type: ignore
except Exception:
    firestore_get_embedding = None
    firestore_put_embedding = None

# --------- Vertex AI provider ---------
_emb_provider = os.getenv("EMB_PROVIDER", "openrouter").lower()
try:
    from vertex_embeddings import get_vertex_embedding  # type: ignore
except Exception:
    get_vertex_embedding = None

def _normalize_text_for_key(text: str) -> str:
    # Normalización ligera para evitar claves distintas por espacios
    return " ".join((text or "").strip().split())

def _make_content_key(text: str) -> str:
    norm = _normalize_text_for_key(text)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()

def _embedding_fingerprint() -> str:
    # Identifica unívocamente el modelo (y por extensión su dimensión)
    s = AppSettings.load()
    return s.embed_model

def _get_embedding_cache_collection():
    client = get_chroma_client()
    name = os.getenv("EMBED_CACHE_COLLECTION", "embedding_cache")
    return client.get_or_create_collection(name=name, metadata={"hnsw:space": "cosine"})

def _lru_get(key_fp: str) -> Optional[List[float]]:
    with _embed_cache_lock:
        vec = _embed_cache_lru.get(key_fp)
        if vec is not None:
            # Mover al final (más reciente)
            _embed_cache_lru.move_to_end(key_fp)
        return vec

def _lru_put(key_fp: str, vec: List[float]) -> None:
    with _embed_cache_lock:
        _embed_cache_lru[key_fp] = vec
        _embed_cache_lru.move_to_end(key_fp)
        if len(_embed_cache_lru) > _embed_cache_capacity:
            # Evict least-recently used
            _embed_cache_lru.popitem(last=False)

def _fs_load(key: str, fingerprint: str) -> Optional[List[float]]:
    if not _emb_fs_cache_enabled:
        return None
    try:
        _emb_fs_cache_dir.mkdir(parents=True, exist_ok=True)
        p = _emb_fs_cache_dir / f"{fingerprint}__{key}.npy"
        if p.is_file():
            import numpy as np  # lazy import
            arr = np.load(str(p))
            vec = arr.tolist()
            logger.info("[EMB][FS] Cache hit (%s)", p.name)
            return vec if isinstance(vec, list) else None
    except Exception as e:
        logger.warning("[EMB][FS] Cache load fallo: %s", e)
    return None

def _fs_store(key: str, fingerprint: str, vec: List[float]) -> None:
    if not _emb_fs_cache_enabled:
        return
    try:
        _emb_fs_cache_dir.mkdir(parents=True, exist_ok=True)
        p = _emb_fs_cache_dir / f"{fingerprint}__{key}.npy"
        import numpy as np  # lazy import
        np.save(str(p), vec)
        logger.info("[EMB][FS] Cache store (%s)", p.name)
    except Exception as e:
        logger.warning("[EMB][FS] Cache store fallo: %s", e)

def _chroma_load(key: str, fingerprint: str) -> Optional[List[float]]:
    try:
        coll = _get_embedding_cache_collection()
        data = coll.get(ids=[key], include=["embeddings", "metadatas"]) or {}
        ids = data.get("ids") or []
        embs = data.get("embeddings") or []
        metas = data.get("metadatas") or []
        if not ids:
            return None
        # Aplanar vectores si vienen anidados
        if isinstance(embs, list) and embs and isinstance(embs[0], list):
            flat = embs[0]
        else:
            flat = embs if isinstance(embs, list) else []
        meta = metas[0] if metas else {}
        fp_ok = (meta or {}).get("fingerprint") == fingerprint
        if flat and fp_ok:
            logger.info("[EMB][DB] Cache hit (id=%s, fp=%s)", key[:10], fingerprint)
            return flat
    except Exception as e:
        logger.warning("[EMB][DB] Cache load fallo: %s", e)
    return None

def _chroma_store(key: str, fingerprint: str, vec: List[float], text: str) -> None:
    try:
        coll = _get_embedding_cache_collection()
        coll.upsert(ids=[key], documents=[text], embeddings=[vec], metadatas=[{"fingerprint": fingerprint, "ts": int(time.time())}])
        logger.info("[EMB][DB] Cache store (id=%s, fp=%s)", key[:10], fingerprint)
    except Exception as e:
        logger.warning("[EMB][DB] Cache store fallo: %s", e)
    
def _firestore_load(key: str, fingerprint: str) -> Optional[List[float]]:
    if not _emb_firestore_enabled or firestore_get_embedding is None:
        return None
    try:
        return firestore_get_embedding(key, fingerprint)
    except Exception as e:
        logger.warning("[EMB][FSDB] Cache load fallo: %s", e)
        return None

def _firestore_store(key: str, fingerprint: str, vec: List[float], text: str) -> None:
    if not _emb_firestore_enabled or firestore_put_embedding is None:
        return
    try:
        ttl = _emb_cache_ttl if _emb_cache_ttl > 0 else None
        firestore_put_embedding(key, fingerprint, vec, text, ttl_seconds=ttl)
    except Exception as e:
        logger.warning("[EMB][FSDB] Cache store fallo: %s", e)

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
    """Inicializa cliente de Chroma.

    Preferencia:
    - Si CHROMA_DB_URL está definido, usar HttpClient con parseo correcto de host/puerto/ssl.
    - Si falla la conexión HTTP, hacer fallback automático a cliente local persistente (CHROMA_DB_PATH).
    - Si no hay URL, usar directamente cliente local.
    """
    global _chroma_client
    if _chroma_client is not None:
        return _chroma_client
    with _chroma_lock:
        if _chroma_client is not None:
            return _chroma_client
        url = os.getenv("CHROMA_DB_URL")
        path = os.getenv("CHROMA_DB_PATH")
        if url:
            try:
                parsed = urlparse(url)
                host = parsed.hostname or url
                port = parsed.port or (443 if (parsed.scheme or "http").lower() == "https" else 80)
                ssl = (parsed.scheme or "http").lower() == "https"
                logger.info(
                    "Inicializando cliente HTTP de ChromaDB (host='%s', port=%s, ssl=%s)…",
                    host,
                    port,
                    ssl,
                )
                _chroma_client = chromadb.HttpClient(host=host, port=port, ssl=ssl, settings=ChromaSettings(anonymized_telemetry=False))
                return _chroma_client
            except Exception as http_exc:
                logger.error("No se pudo conectar a ChromaDB HTTP (%s). Fallback a cliente local.", http_exc, exc_info=True)
                # Continuar al fallback local
        # Fallback local (persistente) usando nueva API de Chroma (PersistentClient)
        persist_dir = path or "/tmp/chroma"
        logger.info("Inicializando cliente local de ChromaDB (persist_directory='%s')…", persist_dir)
        try:
            _chroma_client = chromadb.PersistentClient(path=persist_dir, settings=ChromaSettings(anonymized_telemetry=False))
        except Exception:
            # Compatibilidad con versiones que usan 'persist_directory' como nombre de parámetro
            _chroma_client = chromadb.PersistentClient(persist_directory=persist_dir, settings=ChromaSettings(anonymized_telemetry=False))
        return _chroma_client


def get_topics_collection():
    """Obtiene la colección de temas (parametrizable via TOPICS_COLLECTION)."""
    client = get_chroma_client()
    name = os.getenv("TOPICS_COLLECTION", "topics_collection")
    logger.info("Usando colección de tópicos: '%s'", name)
    return client.get_or_create_collection(name=name, metadata={"hnsw:space": "cosine"})


def get_memory_collection():
    """Obtiene la colección de memoria."""
    client = get_chroma_client()
    return client.get_or_create_collection(name="memory_collection", metadata={"hnsw:space": "cosine"})


def get_embedding(text: str, *, force: bool = False):
    """Obtiene el embedding para un texto, con verificación previa de existencia en cachés.

    Flujo:
    1) LRU (memoria del proceso) → 2) FS opcional → 3) Chroma embedding_cache
    4) Si no existe o force=True: generar y almacenar en LRU/FS/DB
    """
    global _last_embed_error_ts, _embed_model_override
    fingerprint = _embedding_fingerprint()
    key = _make_content_key(text)
    key_fp = f"{fingerprint}:{key}"

    # Cache-first si no hay force
    if not force:
        with Timer("emb_cache_lookup", labels={"stage": "lru"}):
            hit = _lru_get(key_fp)
        if hit is not None:
            logger.info("[EMB] LRU hit (fp=%s)", fingerprint)
            if record_metric: record_metric("emb_cache_hit", 1, {"stage": "lru"})
            return hit
        with Timer("emb_cache_lookup", labels={"stage": "firestore"}):
            hit = _firestore_load(key, fingerprint)
        if hit is not None:
            if record_metric: record_metric("emb_cache_hit", 1, {"stage": "firestore"})
            _lru_put(key_fp, hit)
            return hit
        with Timer("emb_cache_lookup", labels={"stage": "fs"}):
            hit = _fs_load(key, fingerprint)
        if hit is not None:
            if record_metric: record_metric("emb_cache_hit", 1, {"stage": "fs"})
            _lru_put(key_fp, hit)
            return hit
        with Timer("emb_cache_lookup", labels={"stage": "chroma"}):
            hit = _chroma_load(key, fingerprint)
        if hit is not None:
            if record_metric: record_metric("emb_cache_hit", 1, {"stage": "chroma"})
            _lru_put(key_fp, hit)
            return hit

    # Circuit breaker: evita timeouts repetidos durante 60s tras fallo (se puede desactivar por env)
    circuit_disabled = os.getenv("EMBED_DISABLE_CIRCUIT", "0").lower() in {"1", "true", "yes"}
    if (not circuit_disabled) and _last_embed_error_ts and (time.time() - _last_embed_error_ts) < 60:
        logger.warning("Embedding circuit open (recent failures); skipping embedding generation.")
        return None
    s = AppSettings.load()
    model_name = _embed_model_override or s.embed_model
    logger.info(f"[EMB] Cache miss → Generando embedding (provider={_emb_provider}, model={model_name}) para: '{text[:50]}...'")

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

    # Provider routing
    vec: Optional[list] = None
    with Timer("emb_generate", labels={"provider": _emb_provider, "model": model_name}):
        if _emb_provider == "vertex" and get_vertex_embedding is not None:
            vec = get_vertex_embedding(text, model_name)
        else:
            # Intento HTTP-first para mayor compatibilidad; luego SDK
            vec = _http_call(model_name) or _sdk_call(model_name)
    if vec is not None:
         # Store en caches
        _lru_put(key_fp, vec)
        if record_metric: record_metric("emb_cache_store", 1, {"stage": "firestore"})
        _firestore_store(key, fingerprint, vec, text)
        _fs_store(key, fingerprint, vec)
        _chroma_store(key, fingerprint, vec, text)
        if record_metric: record_metric("emb_success", 1, {"dim": len(vec) if isinstance(vec, list) else None})
        return vec

    # Probar candidatos alternativos baratos soportados en OR
    for candidate in _embed_fallback_candidates:
        if candidate == model_name:
            continue
        logger.warning(f"Embedding fallback: probando modelo alternativo '{candidate}'")
        with Timer("emb_generate_fallback", labels={"provider": _emb_provider, "model": candidate}):
            if _emb_provider == "vertex" and get_vertex_embedding is not None:
                # Si el proveedor Vertex falla, probamos OR como fallback
                vec2 = _http_call(candidate) or _sdk_call(candidate)
            else:
                vec2 = _http_call(candidate) or _sdk_call(candidate)
        if vec2 is not None:
            _embed_model_override = candidate
            logger.info(f"Embedding model conmutado dinámicamente a '{candidate}'")
            if vec2 is not None:
                _lru_put(key_fp, vec2)
                _firestore_store(key, fingerprint, vec2, text)
                _fs_store(key, fingerprint, vec2)
                _chroma_store(key, fingerprint, vec2, text)
                if record_metric: record_metric("emb_success", 1, {"dim": len(vec2) if isinstance(vec2, list) else None, "fallback": True})
                return vec2

    _last_embed_error_ts = time.time()
    if record_metric: record_metric("emb_failure", 1, {"provider": _emb_provider, "model": model_name})
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
