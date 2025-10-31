import json
import math
import os
from functools import lru_cache
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import numpy as np
from embeddings_manager import get_embedding, get_chroma_client
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


GOLDSET_MIN_SIMILARITY = float(os.getenv("GOLDSET_MIN_SIMILARITY", "0.82") or 0.82)
GOLDSET_EMBED_PATH = Path(os.getenv("GOLDSET_EMBED_PATH", "data/gold_posts/goldset_embeddings.npz"))
GOLDSET_COLLECTION_NAME = os.getenv("GOLDSET_COLLECTION_NAME", "goldset_collection")
GOLDSET_TEXTS_CACHE: Optional[List[str]] = None
GOLDSET_EMB_CACHE: Optional[List[Sequence[float]]] = None


def _load_embeddings_from_npz(path: Path) -> Optional[Tuple[List[str], List[Sequence[float]]]]:
    if not path.exists():
        return None
    try:
        data = np.load(path, allow_pickle=True)
    except Exception as exc:
        logger.warning("Could not read NPZ at %s: %s", path, exc)
        return None
    texts = data.get("texts")
    documents = data.get("documents")
    embeddings = data.get("embeddings")
    vectors = data.get("vectors")
    texts_arr = texts if texts is not None else documents
    vectors_arr = embeddings if embeddings is not None else vectors
    if texts_arr is None or vectors_arr is None:
        logger.warning("NPZ missing texts/documents or embeddings/vectors. Ignoring.")
        return None
    try:
        texts_list = texts_arr.tolist() if hasattr(texts_arr, "tolist") else list(texts_arr)
        decoded_texts = [t.decode("utf-8") if isinstance(t, bytes) else str(t) for t in texts_list]
        vectors_list = vectors_arr.tolist() if hasattr(vectors_arr, "tolist") else list(vectors_arr)
    except Exception as exc:
        logger.warning("Failed to parse NPZ contents: %s", exc)
        return None
    return decoded_texts, vectors_list


def _load_embeddings_from_chroma() -> Optional[Tuple[List[str], List[Sequence[float]]]]:
    try:
        client = get_chroma_client()
        collection = client.get_or_create_collection(GOLDSET_COLLECTION_NAME, metadata={"hnsw:space": "cosine"})
        result = collection.get(include=["embeddings", "documents"])  # ensure embeddings are returned
        docs = result.get("documents") or []
        embeddings = result.get("embeddings") or []
        if not docs or not embeddings:
            logger.info("Chroma goldset empty or missing embeddings.")
            return None
        texts = [d[0] if isinstance(d, list) else d for d in docs]
        return texts, embeddings
    except Exception as exc:
        logger.warning("Could not load goldset from Chroma: %s", exc)
        return None


def _get_gold_embeddings() -> Tuple[List[str], List[Sequence[float]]]:
    global GOLDSET_TEXTS_CACHE, GOLDSET_EMB_CACHE
    if GOLDSET_TEXTS_CACHE is not None and GOLDSET_EMB_CACHE is not None:
        return GOLDSET_TEXTS_CACHE, GOLDSET_EMB_CACHE

    # 1. Try Chroma collection first (canonical source)
    chroma_data = _load_embeddings_from_chroma()
    if chroma_data:
        GOLDSET_TEXTS_CACHE, GOLDSET_EMB_CACHE = chroma_data
        logger.info("Goldset embeddings loaded from Chroma collection '%s'.", GOLDSET_COLLECTION_NAME)
        return GOLDSET_TEXTS_CACHE, GOLDSET_EMB_CACHE

    # 2. Fallback to NPZ file
    npz_data = _load_embeddings_from_npz(GOLDSET_EMBED_PATH)
    if npz_data:
        GOLDSET_TEXTS_CACHE, GOLDSET_EMB_CACHE = npz_data
        logger.info("Goldset embeddings loaded from NPZ (%s).", GOLDSET_EMBED_PATH)
        return GOLDSET_TEXTS_CACHE, GOLDSET_EMB_CACHE

    # 3. Final fallback: compute on the fly only if explicitly allowed
    allow_runtime = os.getenv("GOLDSET_ALLOW_RUNTIME_COMPUTE", "0").lower() in {"1", "true", "yes", "y"}
    if allow_runtime:
        texts, embeddings = _compute_embeddings_locally()
        GOLDSET_TEXTS_CACHE, GOLDSET_EMB_CACHE = texts, embeddings
        logger.warning("Goldset embeddings computed on the fly. Consider precomputing for stability.")
        return GOLDSET_TEXTS_CACHE, GOLDSET_EMB_CACHE

    logger.error(
        "Goldset embeddings unavailable; set up Chroma or NPZ. Runtime compute disabled (GOLDSET_ALLOW_RUNTIME_COMPUTE=0)."
    )
    GOLDSET_TEXTS_CACHE, GOLDSET_EMB_CACHE = [], []
    return GOLDSET_TEXTS_CACHE, GOLDSET_EMB_CACHE


def _compute_embeddings_locally() -> Tuple[List[str], List[Sequence[float]]]:
    texts = load_gold_texts()
    embeddings: List[Sequence[float]] = []
    valid_texts: List[str] = []
    for text in texts:
        try:
            vec = get_embedding(text)
        except Exception as exc:
            logger.warning("Goldset embedding failed: %s", exc)
            continue
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
    _, gold_embeddings = _get_gold_embeddings()
    if not gold_embeddings:
        return None
    similarities = [_cosine_similarity(vec, gold_vec) for gold_vec in gold_embeddings]
    return max(similarities) if similarities else None
