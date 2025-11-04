"""
Backend de almacenamiento para embeddings en GCP.

Persistencia:
- Firestore (colección configurable) para vector + metadatos
- Opcional: Cloud Storage para guardar .npy por modelo/versión/clave

TTL/Expiración:
- Campo 'expires_at' (timestamp) para aprovechar TTL de Firestore (configuración en consola)

Variables de entorno:
- GCP_PROJECT_ID
- EMB_CACHE_COLLECTION (default: embedding_cache)
- EMB_GCS_BUCKET (opcional)
"""

import os
import time
from typing import Optional, List, Dict
from logger_config import logger

_fs_client = None
_gcs_client = None

def _get_firestore():
    global _fs_client
    if _fs_client is not None:
        return _fs_client
    try:
        from google.cloud import firestore
        _fs_client = firestore.Client(project=os.getenv("GCP_PROJECT_ID"))
    except Exception as e:
        logger.error("No se pudo inicializar Firestore: %s", e)
        _fs_client = None
    return _fs_client

def _get_gcs():
    global _gcs_client
    if _gcs_client is not None:
        return _gcs_client
    try:
        from google.cloud import storage
        _gcs_client = storage.Client(project=os.getenv("GCP_PROJECT_ID"))
    except Exception as e:
        logger.warning("No se pudo inicializar Storage: %s", e)
        _gcs_client = None
    return _gcs_client

def firestore_get_embedding(key: str, fingerprint: str) -> Optional[List[float]]:
    """Lee embedding desde Firestore por clave y fingerprint."""
    client = _get_firestore()
    if client is None:
        return None
    coll_name = os.getenv("EMB_CACHE_COLLECTION", "embedding_cache")
    doc_id = f"{fingerprint}:{key}"
    try:
        doc = client.collection(coll_name).document(doc_id).get()
        if not doc.exists:
            return None
        data = doc.to_dict() or {}
        if data.get("fingerprint") != fingerprint:
            return None
        vec = data.get("vector")
        if isinstance(vec, list):
            logger.info("[EMB][FSDB] Cache hit (doc=%s)", doc_id[:16])
            return vec
        return None
    except Exception as e:
        logger.warning("Firestore get fallo: %s", e)
        return None

def firestore_put_embedding(key: str, fingerprint: str, vec: List[float], text: str, *, ttl_seconds: Optional[int] = None) -> None:
    client = _get_firestore()
    if client is None:
        return
    coll_name = os.getenv("EMB_CACHE_COLLECTION", "embedding_cache")
    doc_id = f"{fingerprint}:{key}"
    now = int(time.time())
    expires_at = (now + int(ttl_seconds)) if ttl_seconds else None
    payload: Dict[str, object] = {
        "fingerprint": fingerprint,
        "vector": vec,
        "text": text,
        "ts": now,
    }
    if expires_at:
        payload["expires_at"] = expires_at
    # Almacenar opcionalmente en GCS para auditoría o tamaño grande
    bucket_name = os.getenv("EMB_GCS_BUCKET")
    if bucket_name:
        try:
            client_gcs = _get_gcs()
            if client_gcs is not None:
                bucket = client_gcs.bucket(bucket_name)
                path = f"embeddings/{fingerprint}/{key}.npy"
                from numpy import array, save
                import io
                buf = io.BytesIO()
                save(buf, array(vec, dtype=float))
                blob = bucket.blob(path)
                blob.upload_from_string(buf.getvalue(), content_type="application/octet-stream")
                payload["gcs_uri"] = f"gs://{bucket_name}/{path}"
                logger.info("[EMB][GCS] Upload %s", path)
        except Exception as e:
            logger.warning("GCS upload fallo: %s", e)
    try:
        client.collection(coll_name).document(doc_id).set(payload)
        logger.info("[EMB][FSDB] Cache store (doc=%s)", doc_id[:16])
    except Exception as e:
        logger.warning("Firestore put fallo: %s", e)

