"""Promueve temas validados en Notion hacia ChromaDB."""

import argparse
import json
import os
from datetime import datetime
from typing import Dict, Optional

from huggingface_ingestion.ingestion import CANDIDATE_INDEX_PATH
from ingestion_config import load_config
from logger_config import logger
from notion_bridge import build_session, query_database, update_page
from persistence_service import persist_topics
from topic_pipeline import TopicRecord


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


def _extract_rich_text(properties: Dict, name: str) -> str:
    entry = properties.get(name)
    if not entry:
        return ""
    rich_list = entry.get("rich_text") or []
    return "".join(block.get("plain_text", "") for block in rich_list)


def _extract_checkbox(properties: Dict, name: str) -> bool:
    entry = properties.get(name)
    if not entry:
        return False
    return bool(entry.get("checkbox"))


def pull_validated_pages(session, database_id: str, status: str) -> list:
    pages = []
    payload = {
        "filter": {
            "property": "Status",
            "select": {"equals": status},
        },
        "page_size": 100,
    }
    start_cursor: Optional[str] = None
    while True:
        if start_cursor:
            payload["start_cursor"] = start_cursor
        data = query_database(session, database_id, payload)
        results = data.get("results") or []
        pages.extend(results)
        start_cursor = data.get("next_cursor")
        if not start_cursor:
            break
    return pages


def build_topic_record(candidate_entry: Dict, page_id: str, approved_status: str) -> TopicRecord:
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


def main():
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

    candidates_index = _load_candidates()
    if not candidates_index:
        return

    session = build_session(token)
    pages = pull_validated_pages(session, database_id, args.status)
    if not pages:
        logger.info("No se encontraron páginas en estado '%s'.", args.status)
        return

    cfg = load_config()
    promoted = 0
    for page in pages:
        properties = page.get("properties") or {}
        if args.synced_property and _extract_checkbox(properties, args.synced_property):
            continue

        candidate_id = _extract_rich_text(properties, "Candidate ID")
        if not candidate_id:
            logger.warning("Página %s sin Candidate ID. Se omite.", page.get("id"))
            continue

        entry = candidates_index.get(candidate_id)
        if not entry:
            logger.warning("Candidate ID %s no encontrado en índice local. Se omite.", candidate_id)
            continue

        record = build_topic_record(entry, page_id=page["id"], approved_status=args.status)
        if args.dry_run:
            logger.info("[Dry-run] Promovería %s (%s).", record.topic_id, record.abstract)
        else:
            summary = persist_topics([record], cfg)
            logger.info(
                "Promovido %s | añadidos=%s saltados=%s errores=%s",
                record.topic_id,
                summary.added,
                summary.skipped,
                summary.errored,
            )
            promoted += summary.added

            entry["topic_record"]["metadata"] = record.metadata
            entry["promoted_at"] = record.metadata["approved_at"]
            candidates_index[candidate_id] = entry

            notion_update = {}
            if args.set_status:
                notion_update["Status"] = {"select": {"name": args.set_status}}
            if args.synced_property:
                notion_update[args.synced_property] = {"checkbox": True}
            if notion_update:
                update_page(session, page["id"], notion_update)

    if not args.dry_run:
        _save_candidates(candidates_index)
    logger.info("Promoción finalizada. Nuevos temas añadidos: %s", promoted)


if __name__ == "__main__":  # pragma: no cover
    main()
