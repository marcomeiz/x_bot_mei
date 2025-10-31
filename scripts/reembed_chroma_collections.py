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


def _chunk(items: List[Any], size: int) -> List[List[Any]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _reembed_collection(src_name: str, dest_name: str, chunk_size: int = 128) -> None:
    client = get_chroma_client()
    src = client.get_or_create_collection(name=src_name, metadata={"hnsw:space": "cosine"})
    logger.info("Leyendo colección origen '%s'…", src_name)
    data = src.get(include=["documents", "ids"]) or {}
    ids = [str(x) for x in (data.get("ids") or [])]
    docs = [str(x) for x in (data.get("documents") or [])]
    if not ids or not docs:
        logger.warning("Colección '%s' vacía o sin documentos; nada que re-embeder.", src_name)
        return
    if len(ids) != len(docs):
        raise ValueError(f"Colección '{src_name}': desalineación ids({len(ids)}) != docs({len(docs)})")

    logger.info("Colección '%s': %s elementos. Calculando nuevos embeddings…", src_name, len(ids))
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
                src_name,
            )
        embeds.extend(vecs)
    if len(embeds) != len(docs):
        logger.warning(
            "Colección '%s': %s/%s embeddings computados; filtrando ids/docs a los válidos.",
            src_name,
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

    # Si el destino es la misma colección, recrear para evitar mismatch de dimensiones
    if dest_name == src_name:
        try:
            client.delete_collection(name=src_name)
            logger.info("Colección '%s' eliminada.", src_name)
        except Exception as e:
            logger.warning("No se pudo eliminar '%s' (continuo): %s", src_name, e)
        time.sleep(0.2)
    dest = client.get_or_create_collection(name=dest_name, metadata={"hnsw:space": "cosine"})
    logger.info("Upserting %s elementos en colección destino '%s'…", len(ids), dest_name)
    # Upsert en bloques
    for i_chunk, (ids_chunk, docs_chunk, emb_chunk) in enumerate(zip(_chunk(ids, chunk_size), _chunk(docs, chunk_size), _chunk(embeds, chunk_size))):
        dest.upsert(ids=ids_chunk, documents=docs_chunk, embeddings=emb_chunk)
        logger.info("Chunk %s upserted (%s items).", i_chunk + 1, len(ids_chunk))
    try:
        total = dest.count()
    except Exception:
        total = None
    logger.info("Colección '%s' re-embebida en '%s'. Total: %s", src_name, dest_name, total if total is not None else "<desconocido>")


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-embed de colecciones Chroma")
    parser.add_argument("--collections", default="topics_collection,memory_collection")
    parser.add_argument("--chunk", type=int, default=128)
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