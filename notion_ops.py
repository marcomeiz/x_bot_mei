"""High-level helpers for interacting with the Notion database used by the radar."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from notion_bridge import build_session, query_database, update_page


@dataclass(frozen=True)
class NotionPage:
    page_id: str
    properties: Dict[str, Any]


def extract_rich_text(properties: Dict[str, Any], name: str) -> str:
    entry = properties.get(name)
    if not entry:
        return ""
    rich_list = entry.get("rich_text") or []
    return "".join(block.get("plain_text", "") for block in rich_list)


def extract_checkbox(properties: Dict[str, Any], name: str) -> bool:
    entry = properties.get(name)
    if not entry:
        return False
    return bool(entry.get("checkbox"))


def fetch_pages_by_status(
    token: str,
    database_id: str,
    status: str,
    page_size: int = 100,
    session=None,
) -> List[NotionPage]:
    session = session or build_session(token)
    return _fetch_pages(session, database_id, {"property": "Status", "select": {"equals": status}}, page_size)


def count_pages_by_status(
    token: str,
    database_id: str,
    status: str,
    page_size: int = 100,
) -> int:
    session = build_session(token)
    pages = fetch_pages_by_status(token, database_id, status, page_size=page_size, session=session)
    return len(pages)


def update_page_properties(
    token: str,
    page_id: str,
    properties: Dict[str, Any],
) -> Dict[str, Any]:
    session = build_session(token)
    return update_page(session, page_id, properties)


def _fetch_pages(session, database_id: str, filter_obj: Dict[str, Any], page_size: int) -> List[NotionPage]:
    pages: List[NotionPage] = []
    payload: Dict[str, Any] = {"filter": filter_obj, "page_size": page_size}
    start_cursor: Optional[str] = None

    while True:
        if start_cursor:
            payload["start_cursor"] = start_cursor
        data = query_database(session, database_id, payload)
        results = data.get("results") or []
        pages.extend([NotionPage(page_id=item["id"], properties=item.get("properties", {})) for item in results])
        start_cursor = data.get("next_cursor")
        if not start_cursor:
            break
    return pages
