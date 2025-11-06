from __future__ import annotations

import json
import os
from typing import Dict, Optional

from diagnostics_logger import diagnostics
from src.telemetry import safe_capture
from embeddings_manager import get_topics_collection


SEED_PATH = os.path.join("data", "topics_seed.jsonl")


def get_health() -> Dict[str, Optional[object]]:
    """Devuelve estado de la colección de temas.

    Campos:
    - exists: bool
    - count: int
    - emb_dim: int | None
    """
    try:
        # Intentar consultar colección si existe
        topics = get_topics_collection()
        try:
            count = int(topics.count())  # type: ignore
        except Exception:
            count = 0
        emb_dim = int(os.getenv("SIM_DIM", "3072") or 3072)
        return {"exists": True, "count": count, "emb_dim": emb_dim}
    except Exception as e:
        diagnostics.error("TOPICS_HEALTH_ERROR", {"err": str(e)})
        return {"exists": False, "count": 0, "emb_dim": None}


def pick_for(user_id: str) -> Optional[Dict[str, object]]:
    """Selección primaria de tema para el usuario.

    Usa Sistema 2: selección por LEJANÍA MÁXIMA del último tweet publicado.
    """
    try:
        # Import here to avoid circular dependencies
        from core_generator import find_relevant_topic
        from logger_config import logger

        logger.info(f"[PICK_FOR] Starting Sistema 2 topic selection for user {user_id}")

        # Call Sistema 2 topic selection
        topic = find_relevant_topic(sample_size=5)

        if not topic:
            logger.warning("[PICK_FOR] Sistema 2 returned None - no topics available or ChromaDB empty")
            diagnostics.warn("PICK_FOR_NO_TOPIC", {"user": user_id, "reason": "find_relevant_topic returned None"})
            return None

        logger.info(f"[PICK_FOR] Sistema 2 selected topic: {topic.get('topic_id')} - {topic.get('abstract', '')[:50]}...")

        # Normalize format
        result = {
            "id": topic.get("topic_id"),
            "text": topic.get("abstract", ""),
            "abstract": topic.get("abstract", ""),
            "source_pdf": topic.get("source_pdf"),
        }

        diagnostics.info("PICK_FOR_SUCCESS", {"user": user_id, "topic_id": result.get("id")})
        return result

    except Exception as e:
        from logger_config import logger
        logger.error(f"[PICK_FOR] Exception during topic selection: {e}", exc_info=True)
        diagnostics.error("PICK_FOR_ERROR", {"err": str(e), "user": user_id, "trace": str(e.__class__.__name__)})
        return None


def _load_seed_entries() -> list[Dict[str, str]]:
    entries: list[Dict[str, str]] = []
    if not os.path.exists(SEED_PATH):
        return entries
    try:
        with open(SEED_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    tid = str(obj.get("id") or obj.get("topic_id") or "").strip()
                    text = str(obj.get("text") or obj.get("abstract") or "").strip()
                    if tid and text:
                        entries.append({"id": tid, "text": text})
                except Exception:
                    continue
    except Exception as e:
        diagnostics.warn("TOPICS_SEED_LOAD_FAILED", {"err": str(e)})
    return entries


def fallback_topic() -> Dict[str, str]:
    """Lee seed local o deriva de goldset si falta.

    Prioridad:
    1) data/topics_seed.jsonl
    2) Derivar entrada mínima estática si falta
    """
    entries = _load_seed_entries()
    if entries:
        # Selección simple: primera entrada
        choice = entries[0]
        return {"id": choice["id"], "text": choice["text"]}

    # Fallback mínimo si seed ausente
    return {"id": "seed:profit-first", "text": "Profit First for solo founders"}


def get_topic_or_fallback(user_id: str) -> Dict[str, object]:
    """Devuelve un tema con fuente (primary|fallback). Nunca None."""
    try:
        t = pick_for(user_id)
        if t and t.get("id"):
            # Normalizar campos esperados aguas abajo
            topic_id = str(t.get("id"))
            abstract = str(t.get("text") or t.get("abstract") or "")
            res = {"id": topic_id, "text": abstract, "source": "primary"}
            diagnostics.info("TOPIC_SELECTED", {"source": "primary", "topic_id": topic_id})
            return res
    except Exception as e:
        diagnostics.error("TOPIC_SELECT_ERROR", {"err": str(e)})

    fb = fallback_topic()
    safe_capture("topic_fallback_used", {"user": user_id})
    diagnostics.warn("TOPIC_SELECTED", {"source": "fallback", "topic_id": fb.get("id")})
    return {"id": fb.get("id"), "text": fb.get("text"), "source": "fallback"}

