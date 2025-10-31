"""
Load gold-standard posts into the goldset Chroma collection.

Usage:
    python scripts/ingest_goldset.py
"""

import json
import os
import sys
from pathlib import Path
from typing import List

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

from embeddings_manager import get_chroma_client  # noqa: E402
from logger_config import logger  # noqa: E402


GOLDSET_COLLECTION_NAME = os.getenv("GOLDSET_COLLECTION_NAME", "goldset_collection")
GOLDSET_FILE = Path(os.getenv("GOLDSET_DATA_PATH", "data/gold_posts/hormozi_master.json"))
ID_PREFIX = "gold_"


def load_gold_posts(path: Path) -> List[str]:
    if not path.exists():
        raise FileNotFoundError(f"Gold set file not found at {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    posts: List[str] = []
    for item in data:
        text = str(item.get("text", "")).strip()
        if text:
            posts.append(text)
    if not posts:
        raise ValueError(f"No posts extracted from {path}")
    return posts


def ingest_gold_posts() -> None:
    posts = load_gold_posts(GOLDSET_FILE)
    client = get_chroma_client()
    collection = client.get_or_create_collection(GOLDSET_COLLECTION_NAME)

    ids = []
    for idx, text in enumerate(posts, start=1):
        ids.append(f"{ID_PREFIX}{idx:04d}")

    collection.upsert(ids=ids, documents=posts)
    logger.info("Gold set ingest completed: %s posts upserted into %s.", len(posts), GOLDSET_COLLECTION_NAME)


if __name__ == "__main__":
    ingest_gold_posts()
