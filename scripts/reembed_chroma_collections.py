"""
Re-embeder colecciones de Chroma con el modelo de embeddings actual.

Uso:
    CHROMA_DB_URL=http://<host>:<port> \
    EMBED_MODEL=openai/text-embedding-3-large \
    python scripts/reembed_chroma_collections.py --collections topics_collection,memory_collection

Notas:
- Recupera documentos e IDs de la colección, calcula nuevos embeddings y re-crea la colección.
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


def _chunk(items: List[Any], size: int) -> List[List[Any]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _reembed_collection(name: str, chunk_size: int = 128) -> None:
    client = get_chroma_client()
    coll = client.get_or_create_collection(name=name, metadata={"hnsw:space": "cosine"})
    logger.info("Leyendo colección '%s'…", name)
    data = coll.get(include=["documents", "ids"]) or {}
    ids = [str(x) for x in (data.get("ids") or [])]
    docs = [str(x) for x in (data.get("documents") or [])]
    if not ids or not docs:
        logger.warning("Colección '%s' vacía o sin documentos; nada que re-embeder.", name)
        return
    if len(ids) != len(docs):
        raise ValueError(f"Colección '{name}': desalineación ids({len(ids)}) != docs({len(docs)})")

    logger.info("Colección '%s': %s elementos. Calculando nuevos embeddings…", name, len(ids))
    embeds: List[List[float]] = []
    for chunk in _chunk(docs, chunk_size):
        vecs = []
        for doc in chunk:
            try:
                v = get_embedding(doc)
            except Exception as e:
                logger.warning("Embedding falló para doc: %s", e)
                v = None
            if v is not None:
                vecs.append(v)
        if len(vecs) != len(chunk):
            logger.warning(
                "Se omitieron %s documentos por fallo de embedding en '%s'.",
                len(chunk) - len(vecs),
                name,
            )
        embeds.extend(vecs)
    if len(embeds) != len(docs):
        logger.warning(
            "Colección '%s': %s/%s embeddings computados; filtrando ids/docs a los válidos.",
            name,
            len(embeds),
            len(docs),
        )
        # Filtrar para mantener alineación
        new_ids: List[str] = []
        new_docs: List[str] = []
        idx = 0
        for doc in docs:
            if idx < len(embeds):
                new_ids.append(ids[idx])
                new_docs.append(doc)
            idx += 1
        ids, docs = new_ids, new_docs

    # Recrear colección para evitar mismatch de dimensiones
    try:
        client.delete_collection(name=name)
        logger.info("Colección '%s' eliminada.", name)
    except Exception as e:
        logger.warning("No se pudo eliminar '%s' (continuo): %s", name, e)
    time.sleep(0.2)
    coll2 = client.get_or_create_collection(name=name, metadata={"hnsw:space": "cosine"})
    logger.info("Re-creada colección '%s'. Upserting %s elementos…", name, len(ids))
    # Upsert en bloques
    for i_chunk, (ids_chunk, docs_chunk, emb_chunk) in enumerate(zip(_chunk(ids, chunk_size), _chunk(docs, chunk_size), _chunk(embeds, chunk_size))):
        coll2.upsert(ids=ids_chunk, documents=docs_chunk, embeddings=emb_chunk)
        logger.info("Chunk %s upserted (%s items).", i_chunk + 1, len(ids_chunk))
    try:
        total = coll2.count()
    except Exception:
        total = None
    logger.info("Colección '%s' re-embebida. Total: %s", name, total if total is not None else "<desconocido>")


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-embed de colecciones Chroma")
    parser.add_argument("--collections", default="topics_collection,memory_collection")
    parser.add_argument("--chunk", type=int, default=128)
    args = parser.parse_args()

    names = [n.strip() for n in args.collections.split(",") if n.strip()]
    logger.info("Colecciones objetivo: %s", names)
    for n in names:
        try:
            _reembed_collection(n, chunk_size=args.chunk)
        except Exception as e:
            logger.error("Fallo re-embedding de '%s': %s", n, e, exc_info=True)


if __name__ == "__main__":
    main()