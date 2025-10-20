"""CLI para captar señales desde Reddit y convertirlas en candidatos."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, Optional

from dataclasses import asdict
import os

import requests

from huggingface_ingestion.evaluator import craft_topic_from_signal, evaluate_signal
from ingestion_config import ensure_directories, load_config
from logger_config import logger
from prompt_context import build_prompt_context
from topic_pipeline import collect_valid_topics

STATE_PATH = Path(Path("db") / "reddit_ingestion_state.json")
CANDIDATE_DIR = Path(Path("json") / "reddit_candidates")
# Compartimos el índice con Hugging Face para que hf_notion_sync lo procese sin cambios.
CANDIDATE_INDEX_PATH = Path("db") / "hf_candidate_records.json"
FEATURE_FLAG_ENV = "ENABLE_REDDIT_INGESTION"
_FLAG_TRUE_VALUES = {"1", "true", "yes", "y", "on"}


def _hash_post_id(post_id: str, text: str) -> str:
    candidate = (post_id or "") + text
    return hashlib.md5(candidate.encode("utf-8")).hexdigest()[:16]


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_state() -> Dict:
    if STATE_PATH.exists():
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception as exc:  # pragma: no cover - corrupción inesperada
            logger.warning("No se pudo leer estado previo de Reddit (%s). Se reinicia.", exc)
    return {}


def _save_state(state: Dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, ensure_ascii=False)


def _load_candidate_index() -> Dict:
    if CANDIDATE_INDEX_PATH.exists():
        try:
            with open(CANDIDATE_INDEX_PATH, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception as exc:
            logger.warning("No se pudo leer índice de candidatos (%s). Se recreará.", exc)
    return {}


def _save_candidate_index(index: Dict) -> None:
    CANDIDATE_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CANDIDATE_INDEX_PATH, "w", encoding="utf-8") as handle:
        json.dump(index, handle, indent=2, ensure_ascii=False)


def _should_include(text: str, include: Iterable[str], exclude: Iterable[str], min_length: int, max_length: int) -> bool:
    lowered = text.lower()
    if include:
        if not any(keyword.lower() in lowered for keyword in include):
            return False
    if any(keyword.lower() in lowered for keyword in exclude):
        return False
    return min_length <= len(lowered) <= max_length


def _within_timeframe(created_utc: float, hours: Optional[int]) -> bool:
    if hours is None:
        return True
    created = datetime.fromtimestamp(created_utc, tz=timezone.utc)
    return created >= datetime.now(tz=timezone.utc) - timedelta(hours=hours)


def _load_config(path: Optional[str]) -> Iterable[Dict]:
    config_path = Path(path or "config/reddit_sources.json")
    if not config_path.exists():
        raise FileNotFoundError(f"No se encontró configuración de Reddit en {config_path}.")
    with open(config_path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, list):
        raise ValueError("La configuración de Reddit debe ser una lista de fuentes.")
    return data


def _fetch_posts(subreddit: str, limit: int) -> Iterable[Dict]:
    url = f"https://www.reddit.com/r/{subreddit}/new.json"
    headers = {"User-Agent": "x-bot-mei/0.1 (+https://github.com/marcomeiz/x_bot_mei)"}
    params = {"limit": max(1, min(limit, 100))}
    resp = requests.get(url, headers=headers, params=params, timeout=15)
    resp.raise_for_status()
    payload = resp.json()
    children = payload.get("data", {}).get("children", [])
    for child in children:
        post = child.get("data") or {}
        if post.get("stickied") or post.get("locked") or post.get("over_18"):
            continue
        yield post


def run_ingestion(config_path: Optional[str] = None, limit: Optional[int] = None) -> Dict:
    if os.getenv(FEATURE_FLAG_ENV, "0").strip().lower() not in _FLAG_TRUE_VALUES:
        logger.info(
            "Ingestión de Reddit en pausa. Define %s=1 para reactivarla en entornos controlados.",
            FEATURE_FLAG_ENV,
        )
        return {
            "processed": 0,
            "accepted": 0,
            "rejected": 0,
            "sources": {},
            "candidates_file": None,
            "feature_status": "paused",
        }

    sources = list(_load_config(config_path))
    ensure_directories((CANDIDATE_DIR,))

    state = _load_state()
    candidate_index = _load_candidate_index()
    watcher_cfg = load_config()
    context = build_prompt_context()

    summary = {"processed": 0, "accepted": 0, "rejected": 0, "sources": {}}
    candidates_buffer = []

    for entry in sources:
        subreddit = entry.get("subreddit")
        if not subreddit:
            logger.warning("Entrada de Reddit sin subreddit definida. Se omite.")
            continue

        name = entry.get("name") or f"reddit_{subreddit}"
        include_kw = entry.get("include_keywords", [])
        exclude_kw = entry.get("exclude_keywords", [])
        min_length = entry.get("min_length", 80)
        max_length = entry.get("max_length", 4000)
        limit_per_source = entry.get("max_posts", 50)
        score_min = entry.get("min_score", 0)
        timeframe_hours = entry.get("timeframe_hours")
        pause_seconds = entry.get("throttle_seconds", 1.0)
        tags = entry.get("tags", [])
        stage = entry.get("stage", "")

        source_stats = {"processed": 0, "accepted": 0, "rejected": 0}
        processed_for_source = 0
        state_bucket = state.setdefault(name, {})

        try:
            posts = list(_fetch_posts(subreddit, limit_per_source))
        except Exception as exc:
            logger.error("No se pudieron recuperar posts de r/%s: %s", subreddit, exc)
            continue

        for post in posts:
            if limit and summary["processed"] >= limit:
                break
            if processed_for_source >= limit_per_source:
                break

            post_id = post.get("id") or ""
            created = post.get("created_utc")
            score = post.get("score", 0)
            url = f"https://reddit.com{post.get('permalink', '')}"
            title = (post.get("title") or "").strip()
            body = (post.get("selftext") or "").strip()
            text = (title + "\n\n" + body).strip()

            summary["processed"] += 1
            source_stats["processed"] += 1
            processed_for_source += 1

            if not text:
                state_bucket[post_id] = {"status": "empty", "updated_at": _timestamp()}
                continue

            if not _within_timeframe(created or 0, timeframe_hours):
                state_bucket[post_id] = {"status": "stale", "updated_at": _timestamp()}
                continue

            if score < score_min:
                state_bucket[post_id] = {"status": "low_score", "score": score, "updated_at": _timestamp()}
                continue

            if not _should_include(text, include_kw, exclude_kw, min_length, max_length):
                state_bucket[post_id] = {"status": "filtered", "updated_at": _timestamp()}
                continue

            row_hash = _hash_post_id(post_id, text)
            if row_hash in state_bucket:
                continue

            evaluation = evaluate_signal(text, f"reddit::{subreddit}", context)
            if not evaluation.all_passed:
                source_stats["rejected"] += 1
                summary["rejected"] += 1
                state_bucket[row_hash] = {
                    "status": "rejected",
                    "answers": evaluation.answers,
                    "updated_at": _timestamp(),
                }
                continue

            topic_payload = craft_topic_from_signal(text, context, extra_tags=tags)
            if not topic_payload:
                source_stats["rejected"] += 1
                summary["rejected"] += 1
                state_bucket[row_hash] = {
                    "status": "no_topic",
                    "answers": evaluation.answers,
                    "updated_at": _timestamp(),
                }
                continue

            base_metadata = {
                "source_type": "reddit",
                "stage": stage,
                "tags": tags,
                "reddit_subreddit": subreddit,
                "reddit_url": url,
                "reddit_score": score,
                "status": "candidate",
                "evaluation": evaluation.answers,
                "pain_point": topic_payload.get("pain_point"),
                "leverage": topic_payload.get("leverage"),
                "raw_snippet": text[:420],
                "source_fields": {
                    "author": post.get("author"),
                    "num_comments": post.get("num_comments"),
                },
            }

            pdf_name = f"reddit::{subreddit}::{row_hash}"
            records = collect_valid_topics(
                topics=[topic_payload["topic"]],
                pdf_name=pdf_name,
                cfg=watcher_cfg,
                context=context,
                base_metadata=base_metadata,
            )

            if not records:
                source_stats["rejected"] += 1
                summary["rejected"] += 1
                state_bucket[row_hash] = {
                    "status": "rejected_validation",
                    "answers": evaluation.answers,
                    "updated_at": _timestamp(),
                }
                continue

            for record in records:
                candidate_entry = {
                    "candidate_id": f"{record.topic_id}",
                    "source": {
                        "name": name,
                        "dataset": "reddit",
                        "split": subreddit,
                    },
                    "topic_record": asdict(record),
                    "topic_payload": topic_payload,
                    "evaluation": evaluation.answers,
                    "ingested_at": _timestamp(),
                }
                candidates_buffer.append(candidate_entry)
                candidate_index[candidate_entry["candidate_id"]] = candidate_entry

            state_bucket[row_hash] = {
                "status": "accepted",
                "answers": evaluation.answers,
                "topic_id": records[0].topic_id,
                "updated_at": _timestamp(),
            }
            source_stats["accepted"] += len(records)
            summary["accepted"] += len(records)

            time.sleep(max(0.0, float(pause_seconds)))

        summary["sources"][name] = source_stats

    candidates_file = None
    if candidates_buffer:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        CANDIDATE_DIR.mkdir(parents=True, exist_ok=True)
        candidates_file = CANDIDATE_DIR / f"reddit_candidates_{timestamp}.jsonl"
        with open(candidates_file, "w", encoding="utf-8") as handle:
            for entry in candidates_buffer:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        logger.info("Candidatos de Reddit guardados en %s", candidates_file)

    summary["candidates_file"] = str(candidates_file) if candidates_file else None
    _save_state(state)
    _save_candidate_index(candidate_index)
    return summary


def main() -> None:  # pragma: no cover - entrada CLI
    parser = argparse.ArgumentParser(description="Ingesta señales operativas desde Reddit.")
    parser.add_argument("--config", help="Ruta a config JSON (default config/reddit_sources.json).")
    parser.add_argument("--limit", type=int, help="Límite total duro de posts a evaluar.")
    parser.add_argument("--notify", action="store_true", help="Envía notificación Telegram si hay candidatos nuevos.")
    args = parser.parse_args()

    summary = run_ingestion(config_path=args.config, limit=args.limit)
    logger.info("Resumen de ingestión Reddit: %s", json.dumps(summary, ensure_ascii=False, indent=2))

    if args.notify and summary.get("accepted"):
        from notifications import send_telegram_message

        sources = ", ".join(sorted(summary.get("sources", {}).keys())) or "subreddits desconocidos"
        send_telegram_message(
            f"📥 Reddit entregó {summary['accepted']} nuevos candidatos desde {sources}."
        )


if __name__ == "__main__":
    main()
