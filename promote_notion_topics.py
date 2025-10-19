"""Promote validated Notion topics into ChromaDB and mark them as synced."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional

from huggingface_ingestion.ingestion import CANDIDATE_INDEX_PATH
from ingestion_config import load_config
from logger_config import logger
from notion_bridge import build_session, update_page
from notion_ops import (
    NotionPage,
    extract_checkbox,
    extract_rich_text,
    fetch_pages_by_status,
)
from persistence_service import persist_topics
from topic_pipeline import TopicRecord


@dataclass(frozen=True)
class PromotionSummary:
    processed: int = 0
    added: int = 0
    skipped: int = 0
    errored: int = 0


def _load_candidates() -> Dict[str, Dict]:
    if not CANDIDATE_INDEX_PATH.exists():
        logger.error("No se encontró el índice de candidatos en %s.", CANDIDATE_INDEX_PATH)
        return {}
    with open(CANDIDATE_INDEX_PATH, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _save_candidates(index: Dict) -> None:
    CANDIDATE_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CANDIDATE_INDEX_PATH, "w", encoding="utf-8") as handle:
        json.dump(index, handle, indent=2, ensure_ascii=False)


def _build_topic_record(candidate_entry: Dict, page_id: str, approved_status: str) -> TopicRecord:
    base = candidate_entry["topic_record"]
    metadata = dict(base.get("metadata") or {})
    metadata.update(
        {
            "status": "approved",
            "approved_at": datetime.utcnow().isoformat(),
            "notion_page_id": page_id,
            "notion_status": approved_status,
        }
    )
    return TopicRecord(
        topic_id=base["topic_id"],
        abstract=base["abstract"],
        source_pdf=base["source_pdf"],
        metadata=metadata,
    )


def promote_validated_topics(
    token: str,
    database_id: str,
    status: str = "Validated",
    set_status: Optional[str] = "Promoted",
    sync_checkbox: Optional[str] = "Synced",
    dry_run: bool = False,
) -> PromotionSummary:
    candidates_index = _load_candidates()
    if not candidates_index:
        return PromotionSummary()

    session = build_session(token)
    pages = fetch_pages_by_status(token, database_id, status, session=session)
    if not pages:
        logger.info("No se encontraron páginas en estado '%s'.", status)
        return PromotionSummary()

    cfg = load_config()
    processed = added = skipped = errored = 0

    for page in pages:
        processed += 1
        properties = page.properties or {}
        if sync_checkbox and extract_checkbox(properties, sync_checkbox):
            skipped += 1
            continue

        candidate_id = extract_rich_text(properties, "Candidate ID")
        if not candidate_id:
            logger.warning("Página %s sin Candidate ID. Se omite.", page.page_id)
            errored += 1
            continue

        entry = candidates_index.get(candidate_id)
        if not entry:
            logger.warning("Candidate ID %s no encontrado en índice local.", candidate_id)
            errored += 1
            continue

        record = _build_topic_record(entry, page_id=page.page_id, approved_status=status)
        if dry_run:
            logger.info("[Dry-run] Promovería %s (%s).", record.topic_id, record.abstract)
            continue

        summary = persist_topics([record], cfg)
        added += summary.added
        skipped += summary.skipped
        errored += summary.errored

        entry["topic_record"]["metadata"] = record.metadata
        entry["promoted_at"] = record.metadata["approved_at"]
        candidates_index[candidate_id] = entry

        notion_update: Dict[str, Dict] = {}
        if set_status:
            notion_update["Status"] = {"select": {"name": set_status}}
        if sync_checkbox:
            notion_update[sync_checkbox] = {"checkbox": True}
        if notion_update:
            update_page(session, page.page_id, notion_update)

    if not dry_run:
        _save_candidates(candidates_index)

    return PromotionSummary(processed=processed, added=added, skipped=skipped, errored=errored)


def main():  # pragma: no cover - CLI entry point
    parser = argparse.ArgumentParser(description="Promueve candidatos validados en Notion.")
    parser.add_argument("--token", help="Token de integración Notion (default NOTION_API_TOKEN).")
    parser.add_argument("--database", help="Database ID (default NOTION_DATABASE_ID).")
    parser.add_argument("--status", default="Validated", help="Estado Notion que se considerará 'listo para promover'.")
    parser.add_argument(
        "--set-status",
        default="Promoted",
        help="Estado Notion posterior a la promoción (select). Si omites, se deja sin cambios.",
    )
    parser.add_argument(
        "--synced-property",
        default="Synced",
        help="Nombre de la propiedad checkbox que se marcará como True tras promover (opcional).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Solo simula, no escribe en Chroma ni Notion.")
    args = parser.parse_args()

    token = args.token or os.getenv("NOTION_API_TOKEN")
    database_id = args.database or os.getenv("NOTION_DATABASE_ID")
    if not token or not database_id:
        raise SystemExit("Configura NOTION_API_TOKEN y NOTION_DATABASE_ID o pasa --token/--database.")

    summary = promote_validated_topics(
        token=token,
        database_id=database_id,
        status=args.status,
        set_status=args.set_status,
        sync_checkbox=args.synced_property,
        dry_run=args.dry_run,
    )

    logger.info(
        "Promoción finalizada. Procesados=%s añadidos=%s saltados=%s errores=%s",
        summary.processed,
        summary.added,
        summary.skipped,
        summary.errored,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
