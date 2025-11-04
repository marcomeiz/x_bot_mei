"""
Backfill/rehidratar Firestore con embeddings persistentes desde colecciones Chroma.

Objetivo:
- Iterar por una o más colecciones de Chroma (topics_collection, memory_collection)
- Para cada documento/texto, invocar get_embedding() con verificación previa
- Almacenar en Firestore (y opcionalmente GCS) utilizando embeddings_manager

Requisitos:
- Variables GCP (GCP_PROJECT_ID, GCP_LOCATION si provider=vertex)
- EMB_PROVIDER=vertex recomendado para GCP nativo
- EMB_CACHE_COLLECTION (Firestore), EMB_GCS_BUCKET (opcional)

Uso:
  python scripts/backfill_firestore_from_chroma.py --collections topics_collection memory_collection --batch-size 128 --limit 0

Notas:
- --limit=0 procesa todo
- Respetará caché: no regenerará embeddings si ya existen en Firestore con fingerprint actual
"""

import os
import time
import argparse
from typing import List
from logger_config import logger
from src.settings import AppSettings

from embeddings_manager import get_chroma_client, get_embedding


def _normalize_text_for_key(text: str) -> str:
    return " ".join((text or "").strip().split())


def run_backfill(collections: List[str], batch_size: int, limit: int) -> None:
    s = AppSettings.load()
    client = get_chroma_client()
    total_processed = 0
    for coll_name in collections:
        try:
            coll = client.get_or_create_collection(name=coll_name, metadata={"hnsw:space": "cosine"})
        except Exception as e:
            logger.error("No se pudo abrir colección '%s': %s", coll_name, e)
            continue
        logger.info("Procesando colección '%s' (batch_size=%d, limit=%d)…", coll_name, batch_size, limit)
        offset = 0
        processed_coll = 0
        while True:
            try:
                chunk = coll.get(limit=batch_size, offset=offset, include=["documents", "metadatas", "ids"]) or {}
            except Exception as e:
                logger.error("Error leyendo chunk (offset=%d) en '%s': %s", offset, coll_name, e)
                break
            docs = chunk.get("documents") or []
            ids = chunk.get("ids") or []
            if not docs:
                break
            for i, doc in enumerate(docs):
                text = _normalize_text_for_key(doc or "")
                if not text:
                    continue
                # Verificación y generación vía embeddings_manager (persistirá en Firestore/FS/Chroma)
                vec = get_embedding(text, force=False)
                if vec is None:
                    logger.warning("No se pudo obtener embedding para doc_id=%s en '%s'", (ids[i] if i < len(ids) else "?"), coll_name)
                processed_coll += 1
                total_processed += 1
                if processed_coll % 50 == 0:
                    logger.info("%s: procesados %d documentos (offset=%d)", coll_name, processed_coll, offset)
                if limit and total_processed >= limit:
                    logger.info("Límite global alcanzado (%d), deteniendo", limit)
                    return
            offset += len(docs)
        logger.info("Colección '%s' completada: %d documentos procesados", coll_name, processed_coll)
    logger.info("Backfill finalizado. Total procesados: %d", total_processed)


def main():
    parser = argparse.ArgumentParser(description="Backfill de Firestore/GCS con embeddings desde Chroma")
    parser.add_argument("--collections", nargs="*", default=["topics_collection"], help="Nombres de colecciones a procesar")
    parser.add_argument("--batch-size", type=int, default=128, help="Tamaño de lote para lectura de Chroma")
    parser.add_argument("--limit", type=int, default=0, help="Límite global de documentos a procesar (0 = sin límite)")
    args = parser.parse_args()

    start = time.time()
    run_backfill(args.collections, args.batch_size, args.limit)
    elapsed = time.time() - start
    logger.info("Tiempo total: %.2fs", elapsed)


if __name__ == "__main__":
    main()

