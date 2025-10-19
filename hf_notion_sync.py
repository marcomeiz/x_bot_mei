"""Sincroniza los candidatos aceptados con una base de datos de Notion."""

import argparse
import json
import os
from typing import Dict, Iterable, Optional

from huggingface_ingestion.ingestion import CANDIDATE_INDEX_PATH
from logger_config import logger
from notion_bridge import build_session, create_page, find_page_by_rich_text, update_page


EVAL_PROPERTY_MAP = {
    "icp_fit": "ICP Fit",
    "actionable": "Actionable",
    "stage_context": "Stage Context",
    "urgency": "Urgency",
}


def _load_candidates() -> Dict[str, Dict]:
    if not CANDIDATE_INDEX_PATH.exists():
        logger.error("No se encontró el índice de candidatos en %s. Ejecuta primero hf_ingestion.py.", CANDIDATE_INDEX_PATH)
        return {}
    with open(CANDIDATE_INDEX_PATH, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _rich_text(value: Optional[str]) -> Dict:
    if not value:
        value = ""
    return {"rich_text": [{"text": {"content": value}}]}


def _title(value: str) -> Dict:
    return {"title": [{"text": {"content": value[:2000]}}]}


def _multi_select(values: Iterable[str]) -> Dict:
    return {"multi_select": [{"name": val} for val in values if val]}


def _select(value: Optional[str]) -> Dict:
    if not value:
        return {"select": None}
    return {"select": {"name": value}}


def _checkbox(flag: bool) -> Dict:
    return {"checkbox": bool(flag)}


def build_properties(entry: Dict, default_status: str) -> Dict:
    topic = entry["topic_record"]["abstract"]
    candidate_id = entry["candidate_id"]
    topic_id = entry["topic_record"]["topic_id"]
    metadata = entry["topic_record"].get("metadata", {})
    evaluation = entry.get("evaluation", {})

    stage = metadata.get("stage")
    tags = metadata.get("tags") or []
    source = metadata.get("hf_source") or metadata.get("source_type")
    dataset = metadata.get("hf_dataset")
    snippet = metadata.get("raw_snippet")
    pain = metadata.get("pain_point")
    leverage = metadata.get("leverage")
    source_fields = metadata.get("source_fields") or {}

    properties = {
        "Name": _title(topic),
        "Status": _select(default_status),
        "Candidate ID": _rich_text(candidate_id),
        "Topic ID": _rich_text(topic_id),
        "Pain": _rich_text(pain),
        "Leverage": _rich_text(leverage),
        "Stage": _select(stage),
        "Tags": _multi_select(tags),
        "Source": _rich_text(str(source)),
        "Dataset": _rich_text(str(dataset)),
        "Snippet": _rich_text(snippet),
        "Source Fields": _rich_text(json.dumps(source_fields, ensure_ascii=False) if source_fields else ""),
    }

    for eval_key, property_name in EVAL_PROPERTY_MAP.items():
        answer = evaluation.get(eval_key, {}).get("answer", False)
        properties[property_name] = _checkbox(bool(answer))

    return properties


def main():
    parser = argparse.ArgumentParser(description="Sincroniza candidatos HF con Notion.")
    parser.add_argument("--token", help="Token de integración de Notion (por defecto NOTION_API_TOKEN).")
    parser.add_argument("--database", help="ID de la base de datos (por defecto NOTION_DATABASE_ID).")
    parser.add_argument(
        "--status",
        default="Review",
        help="Valor de select 'Status' que se asignará al crear/actualizar candidatos (por defecto 'Review').",
    )
    parser.add_argument(
        "--only",
        nargs="*",
        help="Lista opcional de candidate_id específicos a sincronizar.",
    )
    parser.add_argument("--limit", type=int, help="Límite de candidatos a procesar.")
    args = parser.parse_args()

    token = args.token or os.getenv("NOTION_API_TOKEN")
    database_id = args.database or os.getenv("NOTION_DATABASE_ID")
    if not token or not database_id:
        raise SystemExit("Debes configurar NOTION_API_TOKEN y NOTION_DATABASE_ID o pasar --token/--database.")

    candidates = _load_candidates()
    if not candidates:
        return

    session = build_session(token)
    processed = 0

    for candidate_id, entry in candidates.items():
        if args.only and candidate_id not in args.only:
            continue
        properties = build_properties(entry, default_status=args.status)
        existing = find_page_by_rich_text(session, database_id, "Candidate ID", candidate_id)
        if existing:
            page_id = existing["id"]
            update_page(session, page_id, properties)
            logger.info("Actualizado candidato %s en Notion (page=%s).", candidate_id, page_id)
        else:
            create_page(session, database_id, properties)
            logger.info("Creado candidato %s en Notion.", candidate_id)

        processed += 1
        if args.limit and processed >= args.limit:
            break

    logger.info("Proceso completado. Candidatos sincronizados: %s", processed)


if __name__ == "__main__":  # pragma: no cover
    main()
