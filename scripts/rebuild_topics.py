#!/usr/bin/env python3
"""
Deterministic rebuild of the topics collection.

Usage:
  python scripts/rebuild_topics.py --from auto

Features:
- --from seed|goldset|auto (default auto)
- Uses text-embedding-3-large (dim=3072) for parity with goldset
- Emits TOPICS_REBUILT {source,count,emb_dim}
- Deterministic (fixed random seed)
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from typing import Dict, List

from diagnostics_logger import diagnostics
from embeddings_manager import get_embedding, get_topics_collection


random.seed(17)

SEED_JSONL = os.path.join("data", "topics_seed.jsonl")
GOLD_JSON = os.path.join("data", "gold_posts", "hormozi_master.json")

TARGET_DIM = 3072
DEFAULT_TOPICS_COLLECTION = "topics_collection_3072"


def _ensure_env_defaults() -> None:
    # Ensure SIM_DIM consistency for validation inside get_embedding
    os.environ.setdefault("SIM_DIM", str(TARGET_DIM))
    # Ensure collection name targets 3072 parity if not configured
    os.environ.setdefault("TOPICS_COLLECTION", DEFAULT_TOPICS_COLLECTION)


def _load_seed_entries() -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    if not os.path.exists(SEED_JSONL):
        return items
    with open(SEED_JSONL, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                tid = str(obj.get("id") or obj.get("topic_id") or "").strip()
                text = str(obj.get("text") or obj.get("abstract") or "").strip()
                if tid and text:
                    items.append({"id": tid, "text": text})
            except Exception:
                continue
    return items


def _load_gold_entries(limit: int = 100) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    if os.path.exists(GOLD_JSON):
        try:
            with open(GOLD_JSON, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Expect a list of dicts with 'text' or similar
            for i, obj in enumerate(data if isinstance(data, list) else []):
                text = str(obj.get("text") or obj.get("content") or "").strip()
                if not text:
                    continue
                # Compact to a short abstract
                abstract = (text[:220]).strip()
                items.append({"id": f"gold:{i}", "text": abstract})
        except Exception:
            pass
    # Deterministic sample
    if len(items) > limit:
        items = random.sample(items, k=limit)
    return items


def _pick_source(kind: str) -> str:
    kind = (kind or "auto").strip().lower()
    if kind in {"seed", "goldset"}:
        return kind
    # auto: prefer seed if present with reasonable count
    seed_items = _load_seed_entries()
    if len(seed_items) >= 50:
        return "seed"
    return "goldset"


def _build_payload(source: str) -> List[Dict[str, str]]:
    if source == "seed":
        items = _load_seed_entries()
        # Deterministic order: keep file order
        return items
    else:
        # goldset derived entries (deterministic sample)
        return _load_gold_entries(limit=100)


def _embed_all(items: List[Dict[str, str]]) -> List[Dict[str, object]]:
    out: List[Dict[str, object]] = []
    for it in items:
        tid = it.get("id")
        text = it.get("text")
        if not tid or not text:
            continue
        vec = get_embedding(text, model="openai/text-embedding-3-large", force=True)
        if isinstance(vec, list) and len(vec) == TARGET_DIM:
            out.append({"id": tid, "text": text, "vec": vec})
    return out


def _upsert_topics(entries: List[Dict[str, object]]) -> int:
    if not entries:
        return 0
    coll = get_topics_collection()
    # Upsert in batches
    B = 50
    total = 0
    for i in range(0, len(entries), B):
        chunk = entries[i:i+B]
        ids = [str(e["id"]) for e in chunk]
        docs = [str(e["text"]) for e in chunk]
        embs = [e["vec"] for e in chunk]
        try:
            coll.upsert(ids=ids, documents=docs, embeddings=embs, metadatas=[{"source": "rebuild"} for _ in chunk])
            total += len(chunk)
        except Exception as e:
            print(f"Upsert error: {e}", file=sys.stderr)
    return total


def main() -> None:
    _ensure_env_defaults()
    parser = argparse.ArgumentParser(description="Deterministic rebuild of topics collection")
    parser.add_argument("--from", dest="from_source", default="auto", choices=["seed", "goldset", "auto"], help="Source of topics")
    args = parser.parse_args()

    source = _pick_source(args.from_source)
    items = _build_payload(source)
    # Determinism: optional shuffle with fixed seed to spread entries
    random.shuffle(items)
    embedded = _embed_all(items)
    count = _upsert_topics(embedded)

    # Emit structured event and print marker
    diagnostics.info("TOPICS_REBUILT", {"source": source, "count": count, "emb_dim": TARGET_DIM})
    print(f"TOPICS_REBUILT {{'source': '{source}', 'count': {count}, 'emb_dim': {TARGET_DIM}}}")


if __name__ == "__main__":
    main()

