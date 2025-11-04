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
    # Prefer stable providers first (alineado con docs: openai → thenlper → jina)
    "openai/text-embedding-3-small",
    "thenlper/gte-small",
    "jinaai/jina-embeddings-v2-base-en",
)

_embed_client: Optional[OpenAI] = None
_embed_client_lock = threading.Lock()

# Texto actual en curso de embedding; usado por wrappers de SDK/HTTP para compatibilidad con pruebas
_current_text_for_embed: str = ""

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

def _embedding_fingerprint(model_name: str) -> str:
    # Identifica unívocamente el modelo efectivo (y por extensión su dimensión)
    # Usamos el nombre del modelo real para evitar contaminación entre dimensiones.
    return (model_name or "").strip()

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
        # Nota: 'ids' no es un valor válido para 'include' en Chroma; se solicita solo embeddings/metadatas.
        data = coll.get(ids=[key], include=["embeddings", "metadatas"]) or {}
        # Conversión segura a lista para evitar truthiness ambiguo con arrays de NumPy
        def _to_list(x):
            if x is None:
                return []
            if hasattr(x, "tolist"):
                try:
                    return x.tolist()
                except Exception:
                    pass
            try:
                return list(x)
            except Exception:
                return []

        embs = _to_list(data.get("embeddings"))
        metas = _to_list(data.get("metadatas"))
        # Determinar existencia por presencia de vector
        if not embs:
            return None
        # Aplanar vectores si vienen anidados [[vec]]
        if isinstance(embs, list) and embs and isinstance(embs[0], list):
            flat = embs[0]
        else:
            flat = embs if isinstance(embs, list) else []
        meta = metas[0] if (isinstance(metas, list) and metas) else {}
        fp_ok = (meta or {}).get("fingerprint") == fingerprint
        # Validación opcional de dimensión (si se dispone)
        expected_dim = int(os.getenv("SIM_DIM", "0") or 0)
        meta_dim = (meta or {}).get("dim") if isinstance(meta, dict) else None
        dim_ok = True
        if isinstance(meta_dim, int) and meta_dim > 0:
            dim_ok = isinstance(flat, list) and len(flat) == meta_dim
        if expected_dim > 0:
            dim_ok = dim_ok and isinstance(flat, list) and len(flat) == expected_dim

        if isinstance(flat, list) and flat and fp_ok and dim_ok:
            logger.info("[EMB][DB] Cache hit (id=%s, fp=%s)", key[:10], fingerprint)
            return flat
    except Exception as e:
        logger.warning("[EMB][DB] Cache load fallo: %s", e)
    return None

def _chroma_store(key: str, fingerprint: str, vec: List[float], text: str) -> None:
    try:
        coll = _get_embedding_cache_collection()
        coll.upsert(
            ids=[key],
            documents=[text],
            embeddings=[vec],
            metadatas=[{"fingerprint": fingerprint, "dim": len(vec) if isinstance(vec, list) else None, "ts": int(time.time())}],
        )
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
                # Advertir si hay subruta, ya que HttpClient no la utiliza
                if (parsed.path or "").strip("/"):
                    logger.warning(
                        "CHROMA_DB_URL contiene path '%s' que será ignorado por HttpClient; recomendamos exponer Chroma en raíz.",
                        parsed.path,
                    )
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
        persist_dir = path or "db/"
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


def _sdk_call(model: str) -> Optional[list]:
    """Llamada a proveedor SDK (OpenAI/compatible) utilizando el texto activo.

    Nota: La firma acepta solo 'model' para permitir monkeypatch en pruebas.
    El texto a embeber se toma de la variable global '_current_text_for_embed'.
    """
    text = _current_text_for_embed
    try:
        client = _get_embed_client()
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
    """Llamada HTTP a OpenRouter para embeddings usando el texto activo.

    La firma acepta solo 'model' por compatibilidad con pruebas.
    """
    text = _current_text_for_embed
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


def get_embedding(text: str, *, force: bool = False, generate_if_missing: bool = True):
    """Obtiene el embedding para un texto, con verificación previa de existencia en cachés.

    Flujo:
    1) LRU (memoria del proceso) → 2) FS opcional → 3) Chroma embedding_cache
    4) Si no existe o force=True: generar y almacenar en LRU/FS/DB
    """
    global _last_embed_error_ts, _embed_model_override
    # Cargar configuración y decidir el modelo preferido para esta llamada
    s = AppSettings.load()
    global _embed_model_override
    preferred_model = _embed_model_override or s.embed_model
    fingerprint = _embedding_fingerprint(preferred_model)
    key = _make_content_key(text)
    key_fp = f"{fingerprint}:{key}"

    # Cache-first si no hay force
    if not force:
        with Timer("emb_cache_lookup", labels={"stage": "lru"}):
            hit = _lru_get(key_fp)
        if hit is not None:
            expected_dim = int(os.getenv("SIM_DIM", "0") or 0)
            if expected_dim > 0 and (not isinstance(hit, list) or len(hit) != expected_dim):
                logger.warning("[EMB] LRU hit con dimensión inesperada (len=%s != %s); ignorando entrada.", len(hit) if isinstance(hit, list) else None, expected_dim)
            else:
                logger.info("[EMB] LRU hit (fp=%s)", fingerprint)
                if record_metric: record_metric("emb_cache_hit", 1, {"stage": "lru"})
                return hit
        with Timer("emb_cache_lookup", labels={"stage": "firestore"}):
            hit = _firestore_load(key, fingerprint)
        if hit is not None:
            if record_metric: record_metric("emb_cache_hit", 1, {"stage": "firestore"})
            expected_dim = int(os.getenv("SIM_DIM", "0") or 0)
            if expected_dim == 0 or (isinstance(hit, list) and len(hit) == expected_dim):
                _lru_put(key_fp, hit)
                return hit
        with Timer("emb_cache_lookup", labels={"stage": "fs"}):
            hit = _fs_load(key, fingerprint)
        if hit is not None:
            if record_metric: record_metric("emb_cache_hit", 1, {"stage": "fs"})
            expected_dim = int(os.getenv("SIM_DIM", "0") or 0)
            if expected_dim == 0 or (isinstance(hit, list) and len(hit) == expected_dim):
                _lru_put(key_fp, hit)
                return hit
        with Timer("emb_cache_lookup", labels={"stage": "chroma"}):
            hit = _chroma_load(key, fingerprint)
        if hit is not None:
            if record_metric: record_metric("emb_cache_hit", 1, {"stage": "chroma"})
            expected_dim = int(os.getenv("SIM_DIM", "0") or 0)
            if expected_dim == 0 or (isinstance(hit, list) and len(hit) == expected_dim):
                _lru_put(key_fp, hit)
                return hit

    # Antes de generar: respetar política de no-generación cuando aplique
    if not generate_if_missing and not force:
        logger.info("[EMB] Cache miss → Política activa: NO generar embedding (generate_if_missing=False).")
        if record_metric: record_metric("emb_skip_due_to_policy", 1, {"reason": "generate_if_missing_false"})
        return None

    # Circuit breaker: evita timeouts repetidos durante 60s tras fallo (se puede desactivar por env)
    circuit_disabled = os.getenv("EMBED_DISABLE_CIRCUIT", "0").lower() in {"1", "true", "yes"}
    if (not circuit_disabled) and _last_embed_error_ts and (time.time() - _last_embed_error_ts) < 60:
        logger.warning("Embedding circuit open (recent failures); skipping embedding generation.")
        if record_metric: record_metric("emb_skip_due_to_policy", 1, {"reason": "circuit_open"})
        return None
    # Reset override por llamada; comenzamos con el modelo preferido
    _embed_model_override = None
    model_name = preferred_model
    logger.info(f"[EMB] Cache miss → Generando embedding (provider={_emb_provider}, model={model_name}) para: '{text[:50]}...'")

    # Setear texto actual para llamadas de proveedor
    global _current_text_for_embed
    _current_text_for_embed = text

    # Provider routing
    vec: Optional[list] = None
    with Timer("emb_generate", labels={"provider": _emb_provider, "model": model_name}):
        if _emb_provider == "vertex" and get_vertex_embedding is not None:
            vec = get_vertex_embedding(text, model_name)
        else:
            # Intento HTTP-first para mayor compatibilidad; luego SDK
            vec = _http_call(model_name) or _sdk_call(model_name)
    if vec is not None:
        # Validar dimensión si está configurada
        expected_dim = int(os.getenv("SIM_DIM", "0") or 0)
        if expected_dim > 0 and (not isinstance(vec, list) or len(vec) != expected_dim):
            logger.error("Embedding con dimensión inesperada (len=%s != %s); no se almacenará ni retornará.", len(vec) if isinstance(vec, list) else None, expected_dim)
            vec = None
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
            expected_dim = int(os.getenv("SIM_DIM", "0") or 0)
            if expected_dim > 0 and (not isinstance(vec2, list) or len(vec2) != expected_dim):
                logger.error("Embedding fallback con dimensión inesperada (len=%s != %s); se descarta.", len(vec2) if isinstance(vec2, list) else None, expected_dim)
                continue
            _embed_model_override = candidate
            logger.info(f"Embedding model conmutado dinámicamente a '{candidate}'")
            # Actualizar fingerprint y key para el modelo efectivo
            eff_fp = _embedding_fingerprint(candidate)
            eff_key_fp = f"{eff_fp}:{key}"
            _lru_put(eff_key_fp, vec2)
            _firestore_store(key, eff_fp, vec2, text)
            _fs_store(key, eff_fp, vec2)
            _chroma_store(key, eff_fp, vec2, text)
            if record_metric: record_metric("emb_success", 1, {"dim": len(vec2) if isinstance(vec2, list) else None, "fallback": True})
            return vec2

    _last_embed_error_ts = time.time()
    if record_metric: record_metric("emb_failure", 1, {"provider": _emb_provider, "model": model_name})
    return None

## Deprecated: find_similar_topics fue eliminada por no usarse y para evitar violar la política /g.
