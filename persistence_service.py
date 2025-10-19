import json
import os
import time
from dataclasses import dataclass
from typing import List

import requests

from embeddings_manager import get_embedding, get_topics_collection
from logger_config import logger
from ingestion_config import WatcherConfig
from topic_pipeline import TopicRecord


@dataclass(frozen=True)
class PersistenceSummary:
    sent: int
    added: int
    skipped: int
    errored: int


def persist_topics(records: List[TopicRecord], cfg: WatcherConfig) -> PersistenceSummary:
    if not records:
        return PersistenceSummary(sent=0, added=0, skipped=0, errored=0)

    topics_collection = get_topics_collection()
    embeddings_payload = []
    for record in records:
        embedding = get_embedding(record.abstract)
        if embedding is None:
            logger.warning("No se pudo generar embedding para '%s...'.", record.abstract[:40])
            continue
        embeddings_payload.append((embedding, record))

    if not embeddings_payload:
        logger.warning("No hay embeddings válidos para persistir.")
        return PersistenceSummary(sent=0, added=0, skipped=0, errored=len(records))

    ids = [payload[1].topic_id for payload in embeddings_payload]
    existing_ids = _existing_ids(topics_collection, ids)

    entries_to_add = [payload for payload in embeddings_payload if payload[1].topic_id not in existing_ids]

    if entries_to_add:
        topics_collection.add(
            embeddings=[item[0] for item in entries_to_add],
            documents=[item[1].abstract for item in entries_to_add],
            ids=[item[1].topic_id for item in entries_to_add],
            metadatas=[item[1].metadata for item in entries_to_add],
        )
        logger.info("%s temas añadidos a topics_collection.", len(entries_to_add))
    else:
        logger.info("No hay temas nuevos para añadir tras filtrar duplicados.")

    skipped = len(embeddings_payload) - len(entries_to_add)
    errors = len(records) - len(embeddings_payload)

    if cfg.remote_ingest_url and cfg.admin_api_token and entries_to_add:
        _sync_remote(entries_to_add, cfg)

    return PersistenceSummary(
        sent=len(entries_to_add),
        added=len(entries_to_add),
        skipped=skipped,
        errored=errors,
    )


def write_summary_json(pdf_name: str, records: List[TopicRecord], json_dir: str) -> str:
    output_path = os.path.join(json_dir, f"{pdf_name}.json")
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "pdf_name": pdf_name,
                "extracted_topics": [record.__dict__ for record in records],
            },
            handle,
            indent=2,
            ensure_ascii=False,
        )
    return output_path


def _existing_ids(collection, ids: List[str]) -> set:
    try:
        resp = collection.get(ids=ids, include=[])
        rid = resp.get("ids") if isinstance(resp, dict) else None
        if rid:
            return set(_flatten_ids(rid))
    except Exception as exc:
        logger.warning("No se pudo verificar duplicados en la base de datos: %s", exc)
    return set()


def _flatten_ids(raw_ids) -> List[str]:
    if isinstance(raw_ids, list) and raw_ids and isinstance(raw_ids[0], list):
        return [item for sub in raw_ids for item in sub]
    if isinstance(raw_ids, list):
        return raw_ids
    return []


def _sync_remote(entries_to_add: List[tuple], cfg: WatcherConfig) -> None:
    logger.info(
        "Sync remoto: enviando %s temas en lotes de %s…",
        len(entries_to_add),
        cfg.remote_batch,
    )
    for start in range(0, len(entries_to_add), cfg.remote_batch):
        batch = entries_to_add[start : start + cfg.remote_batch]
        payload = {
            "topics": [
                {
                    "id": record.topic_id,
                    "abstract": record.abstract,
                    "pdf": record.source_pdf,
                }
                for _, record in batch
            ]
        }
        url = cfg.remote_ingest_url
        url = f"{url}&token={cfg.admin_api_token}" if "?" in url else f"{url}?token={cfg.admin_api_token}"

        attempt = 0
        while True:
            attempt += 1
            try:
                response = requests.post(url, json=payload, timeout=cfg.remote_timeout)
                if response.status_code == 200:
                    try:
                        data = response.json()
                    except Exception:
                        data = {}
                    logger.info(
                        " · Lote %s: added=%s skipped=%s",
                        (start // cfg.remote_batch) + 1,
                        data.get("added"),
                        data.get("skipped_existing"),
                    )
                    break
                logger.warning(
                    " · Lote %s: HTTP %s -> %s",
                    (start // cfg.remote_batch) + 1,
                    response.status_code,
                    response.text[:200],
                )
            except Exception as exc:
                if attempt < cfg.remote_retries:
                    logger.warning(
                        " · Lote %s: error '%s'. Reintentando (%s/%s)…",
                        (start // cfg.remote_batch) + 1,
                        exc,
                        attempt,
                        cfg.remote_retries,
                    )
                    time.sleep(min(2 * attempt, 6))
                    continue
                logger.error(" · Lote %s: fallo definitivo: %s", (start // cfg.remote_batch) + 1, exc)
                break
