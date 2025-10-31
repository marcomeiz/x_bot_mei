"""
Re-embeder colecciones de Chroma con el modelo de embeddings actual.

Uso:
    CHROMA_DB_URL=http://<host>:<port> \
    EMBED_MODEL=openai/text-embedding-3-large \
    python scripts/reembed_chroma_collections.py --collections topics_collection,memory_collection --chunk 128
    # Para crear nuevas colecciones destino (ej: topics_collection_3072):
    python scripts/reembed_chroma_collections.py --collections topics_collection --dest-suffix _3072

Notas:
- Recupera documentos e IDs de la colección origen, calcula nuevos embeddings y re-crea la colección
  (si el destino es el mismo nombre) o upserta en una nueva colección (si dest != origen).
- Útil para migrar de 1536 → 3072 dimensiones (text-embedding-3-small → large).
"""

import argparse
import os
import sys
import time
from pathlib import Path
from typing import List, Dict, Any

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

from embeddings_manager import get_chroma_client, get_embedding  # noqa: E402
from logger_config import logger  # noqa: E402
import numpy as np  # noqa: E402


def _chunk(items: List[Any], size: int) -> List[List[Any]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _l2_normalize(vec: List[float]) -> List[float]:
    arr = np.array(vec, dtype=float)
    norm = np.linalg.norm(arr)
    if norm == 0:
        return arr.tolist()
    return (arr / norm).tolist()


def _iter_pages(src, page_size: int):
    offset = 0
    while True:
        data = src.get(include=["documents", "metadatas"], limit=page_size, offset=offset) or {}
        ids = data.get("ids") or []
        docs = data.get("documents") or []
        metas = data.get("metadatas") or []
        if not ids:
            break
        yield ids, docs, metas
        offset += len(ids)


def _reembed_collection(src_name: str, dest_name: str, chunk_size: int = 256) -> None:
    client = get_chroma_client()
    src = client.get_or_create_collection(name=src_name, metadata={"hnsw:space": "cosine"})
    dest = client.get_or_create_collection(name=dest_name, metadata={"hnsw:space": "cosine"})

    logger.info("Leyendo y migrando colección '%s' → '%s' (batch<=%s)…", src_name, dest_name, chunk_size)

    total_src = 0
    total_upsert = 0

    # Re-embed con paginación y upsert conservando IDs y metadatos mínimos requeridos
    for ids, docs, metas in _iter_pages(src, page_size=chunk_size):
        # Normalizar documentos por si vienen como listas
        norm_docs: List[str] = []
        for d in docs:
            norm_docs.append(d[0] if isinstance(d, list) else str(d))
        # Preparar metadatos (pattern y topic)
        norm_metas: List[Dict[str, Any]] = []
        for m in metas:
            m = m or {}
            meta_out: Dict[str, Any] = {}
            # Chroma requiere dict no vacío y valores primitivos; usar string vacío si falta
            meta_out["pattern"] = m.get("pattern") if m.get("pattern") is not None else ""
            meta_out["topic"] = m.get("topic") if m.get("topic") is not None else ""
            norm_metas.append(meta_out)
        total_src += len(ids)

        # Generar embeddings 3072 y L2-normalizar
        embeds: List[List[float]] = []
        ok_ids: List[str] = []
        ok_docs: List[str] = []
        ok_metas: List[Dict[str, Any]] = []
        for i, doc in enumerate(norm_docs):
            try:
                v = get_embedding(doc)
            except Exception as e:
                logger.warning("Embedding falló para doc idx=%s: %s", i, e)
                v = None
            if v is None:
                continue
            embeds.append(_l2_normalize(v))
            ok_ids.append(str(ids[i]))
            ok_docs.append(doc)
            ok_metas.append(norm_metas[i])

        if not ok_ids:
            logger.warning("Página sin embeddings válidos; se omite upsert (ids desde offset acumulado=%s).", total_src)
            continue

        # Upsert en sub-batches para evitar timeouts (<= chunk_size)
        for ids_c, docs_c, emb_c, metas_c in zip(_chunk(ok_ids, chunk_size), _chunk(ok_docs, chunk_size), _chunk(embeds, chunk_size), _chunk(ok_metas, chunk_size)):
            dest.upsert(ids=ids_c, documents=docs_c, embeddings=emb_c, metadatas=metas_c)
            total_upsert += len(ids_c)
            logger.info("Upsert chunk: +%s (acumulado=%s)", len(ids_c), total_upsert)

    # Verificaciones automáticas (post-job)
    try:
        dest_data = dest.get(include=["embeddings", "documents", "metadatas"], limit=5) or {}
        dest_vecs = dest_data.get("embeddings") or []
        dest_docs = dest_data.get("documents") or []
        dest_metas = dest_data.get("metadatas") or []
        sample_dim = (len(dest_vecs[0]) if dest_vecs else None)
        dest_count = dest.count()
    except Exception as e:
        logger.warning("Verificación destino falló: %s", e)
        sample_dim, dest_count, dest_docs, dest_metas = None, None, [], []

    logger.info("POST-JOB: '%s' dim=%s count=%s (src_count=%s)", dest_name, sample_dim, dest_count, total_src)
    if sample_dim != 3072:
        logger.warning("Dimensión de destino != 3072 (=%s)", sample_dim)
    if dest_count is not None and total_src and dest_count < min(total_src, 200):
        logger.warning("Conteo destino (%s) menor que esperado (src=%s o >=200)", dest_count, total_src)

    # Muestreo de 5 docs para validar document y pattern
    for i in range(min(5, len(dest_docs))):
        d = dest_docs[i]
        m = dest_metas[i] if i < len(dest_metas) else {}
        has_doc = bool(d)
        has_pattern = (m or {}).get("pattern") is not None
        logger.info("SAMPLE[%s]: has_doc=%s has_pattern=%s topic=%s", i, has_doc, has_pattern, (m or {}).get("topic"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-embed de colecciones Chroma")
    parser.add_argument("--collections", default="topics_collection,memory_collection")
    parser.add_argument("--chunk", type=int, default=256)
    parser.add_argument("--dest-suffix", default="", help="Sufijo para crear colecciones destino (ej: _3072). Si vacío, re-embed in-place.")
    args = parser.parse_args()

    names = [n.strip() for n in args.collections.split(",") if n.strip()]
    logger.info("Colecciones objetivo: %s", names)
    for n in names:
        dest_name = n + args.dest_suffix if args.dest_suffix else n
        try:
            _reembed_collection(n, dest_name, chunk_size=args.chunk)
        except Exception as e:
            logger.error("Fallo re-embedding de '%s'→'%s': %s", n, dest_name, e, exc_info=True)


if __name__ == "__main__":
    main()