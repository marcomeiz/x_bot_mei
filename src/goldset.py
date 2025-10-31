import json
import math
import os
from functools import lru_cache
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from embeddings_manager import get_embedding
from logger_config import logger

DEFAULT_GOLDSET_PATH = Path("data/gold_posts/hormozi_master.json")
GOLDSET_MIN_SIMILARITY = float(os.getenv("GOLDSET_MIN_SIMILARITY", "0.82") or 0.82)


@lru_cache(maxsize=1)
def load_gold_texts(path: Path = DEFAULT_GOLDSET_PATH) -> List[str]:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    posts: List[str] = []
    for item in data:
        text = str(item.get("text", "")).strip()
        if text:
            posts.append(text)
    return posts


def _cosine_similarity(vec_a: Sequence[float], vec_b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(x * x for x in vec_a))
    norm_b = math.sqrt(sum(y * y for y in vec_b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


@lru_cache(maxsize=1)
def _gold_embeddings() -> Tuple[List[str], List[Sequence[float]]]:
    texts = load_gold_texts()
    embeddings: List[Sequence[float]] = []
    valid_texts: List[str] = []
    for text in texts:
        try:
            vec = get_embedding(text)
        except Exception as exc:  # pragma: no cover - network/LLM issues
            logger.warning("Goldset embedding failed: %s", exc)
            vec = None
        if vec:
            embeddings.append(vec)
            valid_texts.append(text)
    if not embeddings:
        logger.error("No embeddings computed for gold set; similarity checks will fallback to 0.")
    return valid_texts, embeddings


def max_similarity_to_goldset(text: str) -> Optional[float]:
    if not text or not text.strip():
        return None
    try:
        vec = get_embedding(text)
    except Exception as exc:  # pragma: no cover
        logger.warning("Could not embed draft for goldset comparison: %s", exc)
        return None
    if not vec:
        return None
    _, gold_embeddings = _gold_embeddings()
    if not gold_embeddings:
        return None
    similarities = [_cosine_similarity(vec, gold_vec) for gold_vec in gold_embeddings]
    return max(similarities) if similarities else None
