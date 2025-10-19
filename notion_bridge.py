"""Helpers to interact with the Notion API using the requests library."""

from __future__ import annotations

import json
from typing import Dict, Optional

import requests

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


def build_session(token: str) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }
    )
    return session


def query_database(
    session: requests.Session,
    database_id: str,
    payload: Optional[Dict] = None,
) -> Dict:
    response = session.post(
        f"{NOTION_API_BASE}/databases/{database_id}/query",
        data=json.dumps(payload or {}),
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def find_page_by_rich_text(
    session: requests.Session,
    database_id: str,
    property_name: str,
    value: str,
) -> Optional[Dict]:
    payload = {
        "filter": {
            "property": property_name,
            "rich_text": {"equals": value},
        }
    }
    data = query_database(session, database_id, payload)
    results = data.get("results") or []
    return results[0] if results else None


def create_page(session: requests.Session, database_id: str, properties: Dict) -> Dict:
    payload = {
        "parent": {"database_id": database_id},
        "properties": properties,
    }
    response = session.post(f"{NOTION_API_BASE}/pages", data=json.dumps(payload), timeout=30)
    response.raise_for_status()
    return response.json()


def update_page(session: requests.Session, page_id: str, properties: Dict) -> Dict:
    payload = {
        "properties": properties,
    }
    response = session.patch(f"{NOTION_API_BASE}/pages/{page_id}", data=json.dumps(payload), timeout=30)
    response.raise_for_status()
    return response.json()
