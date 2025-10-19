import hashlib
import json
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from ingestion_config import ensure_directories, load_config
from logger_config import logger
from prompt_context import build_prompt_context
from topic_pipeline import TopicRecord, collect_valid_topics

from .evaluator import craft_topic_from_signal, evaluate_signal
from .sources import HuggingFaceSourceConfig, load_sources_config

try:
    from datasets import load_dataset  # type: ignore
except ImportError as exc:  # pragma: no cover - import guard
    load_dataset = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


STATE_PATH = Path(os.getenv("HF_STATE_PATH", "db/hf_ingestion_state.json"))
CANDIDATE_DIR = Path(os.getenv("HF_CANDIDATE_DIR", "json/hf_candidates"))
CANDIDATE_INDEX_PATH = Path(os.getenv("HF_CANDIDATE_INDEX", "db/hf_candidate_records.json"))


def _load_state() -> Dict:
    if STATE_PATH.exists():
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception as exc:
            logger.warning("No se pudo leer estado previo de Hugging Face (%s). Se reinicia.", exc)
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
            logger.warning("No se pudo leer índice de candidatos HF (%s). Se recreará.", exc)
    return {}


def _save_candidate_index(index: Dict) -> None:
    CANDIDATE_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CANDIDATE_INDEX_PATH, "w", encoding="utf-8") as handle:
        json.dump(index, handle, indent=2, ensure_ascii=False)


def _state_bucket(state: Dict, source: str) -> Dict:
    if "sources" not in state:
        state["sources"] = {}
    if source not in state["sources"]:
        state["sources"][source] = {}
    return state["sources"][source]


def _hash_row_id(raw_id: Optional[str], text: str) -> str:
    candidate = raw_id or text
    return hashlib.md5(candidate.encode("utf-8")).hexdigest()[:16]


def _extract_metadata_fields(item: Dict, fields) -> Dict:
    result = {}
    for field in fields:
        if field in item:
            value = item[field]
            if isinstance(value, (str, int, float, bool)) or value is None:
                result[field] = value
            else:
                result[field] = str(value)
    return result


def _topic_record_to_dict(record: TopicRecord) -> Dict:
    data = asdict(record)
    # dataclasses.asdict handles nested dict but we ensure metadata present.
    return data


def run_ingestion(config_path: Optional[str] = None, limit: Optional[int] = None) -> Dict:
    """Main entry point to pull Hugging Face datasets and stage candidate topics."""

    if load_dataset is None:
        raise RuntimeError(
            "Falta dependencia 'datasets' para ingestión Hugging Face. Instálala con `pip install datasets`. "
            f"Detalle del error: {_IMPORT_ERROR}"
        )

    sources = load_sources_config(config_path)
    if not sources:
        logger.info("Sin fuentes Hugging Face configuradas. Nada que hacer.")
        return {"processed": 0, "accepted": 0, "rejected": 0, "candidates_file": None}

    ensure_directories((CANDIDATE_DIR,))
    state = _load_state()
    candidate_index = _load_candidate_index()
    watcher_cfg = load_config()
    context = build_prompt_context()

    summary = {"processed": 0, "accepted": 0, "rejected": 0, "sources": {}}
    candidates_buffer = []

    for source in sources:
        load_kwargs = {}
        if source.data_files:
            load_kwargs["data_files"] = source.data_files
        try:
            dataset = load_dataset(source.dataset, split=source.split, **load_kwargs)
        except Exception as exc:
            logger.error("No se pudo cargar dataset %s (%s): %s", source.dataset, source.split, exc)
            continue

        source_stats = {"processed": 0, "accepted": 0, "rejected": 0}
        processed_for_source = 0
        state_bucket = _state_bucket(state, source.name)

        for item in dataset:
            if limit and summary["processed"] >= limit:
                break
            if processed_for_source >= source.max_examples:
                break

            summary["processed"] += 1
            source_stats["processed"] += 1
            processed_for_source += 1

            text = str(item.get(source.text_field, "")).strip()
            if not text or not source.should_include(text):
                row_hash = _hash_row_id(str(item.get(source.id_field, "")), text or repr(item)[:120])
                state_bucket[row_hash] = {
                    "status": "filtered",
                    "updated_at": datetime.utcnow().isoformat(),
                }
                continue

            row_hash = _hash_row_id(str(item.get(source.id_field, "")), text)
            if row_hash in state_bucket:
                continue

            evaluation = evaluate_signal(text, source.name, context)
            if not evaluation.all_passed:
                source_stats["rejected"] += 1
                state_bucket[row_hash] = {
                    "status": "rejected",
                    "answers": evaluation.answers,
                    "updated_at": datetime.utcnow().isoformat(),
                }
                summary["rejected"] += 1
                continue

            topic_payload = craft_topic_from_signal(text, context, extra_tags=source.tags)
            if not topic_payload:
                source_stats["rejected"] += 1
                state_bucket[row_hash] = {
                    "status": "no_topic",
                    "answers": evaluation.answers,
                    "updated_at": datetime.utcnow().isoformat(),
                }
                summary["rejected"] += 1
                continue

            base_metadata = source.base_metadata()
            base_metadata.update(
                {
                    "source_type": "huggingface",
                    "status": "candidate",
                    "hf_row_id": row_hash,
                    "evaluation": evaluation.answers,
                    "pain_point": topic_payload.get("pain_point"),
                    "leverage": topic_payload.get("leverage"),
                    "raw_snippet": text[: source.snippet_length],
                    "source_fields": _extract_metadata_fields(item, source.metadata_fields),
                }
            )

            if topic_payload.get("tags"):
                tags = list(base_metadata.get("tags", []))
                for tag in topic_payload["tags"]:
                    if tag not in tags:
                        tags.append(tag)
                base_metadata["tags"] = tags

            pdf_name = f"hf::{source.name}::{row_hash}"
            records = collect_valid_topics(
                topics=[topic_payload["topic"]],
                pdf_name=pdf_name,
                cfg=watcher_cfg,
                context=context,
                base_metadata=base_metadata,
            )

            if not records:
                source_stats["rejected"] += 1
                state_bucket[row_hash] = {
                    "status": "rejected_validation",
                    "answers": evaluation.answers,
                    "updated_at": datetime.utcnow().isoformat(),
                }
                summary["rejected"] += 1
                continue

            for record in records:
                candidate_entry = {
                    "candidate_id": f"{record.topic_id}",
                    "source": {
                        "name": source.name,
                        "dataset": source.dataset,
                        "split": source.split,
                    },
                    "topic_record": _topic_record_to_dict(record),
                    "topic_payload": topic_payload,
                    "evaluation": evaluation.answers,
                    "ingested_at": datetime.utcnow().isoformat(),
                }
                candidates_buffer.append(candidate_entry)
                candidate_index[candidate_entry["candidate_id"]] = candidate_entry

            source_stats["accepted"] += len(records)
            summary["accepted"] += len(records)
            state_bucket[row_hash] = {
                "status": "accepted",
                "answers": evaluation.answers,
                "topic_id": records[0].topic_id,
                "updated_at": datetime.utcnow().isoformat(),
            }

        summary["sources"][source.name] = source_stats

    candidates_file = None
    if candidates_buffer:
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        candidates_file = CANDIDATE_DIR / f"hf_candidates_{timestamp}.jsonl"
        with open(candidates_file, "w", encoding="utf-8") as handle:
            for entry in candidates_buffer:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        logger.info("Candidatos Hugging Face guardados en %s", candidates_file)

    summary["candidates_file"] = str(candidates_file) if candidates_file else None
    _save_state(state)
    _save_candidate_index(candidate_index)
    return summary
