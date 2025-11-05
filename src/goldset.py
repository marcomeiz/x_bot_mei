import json
import math
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import numpy as np

from diagnostics_logger import emit_structured
from embeddings_manager import get_embedding
from logger_config import logger
from src.normalization import normalize_for_embedding

DEFAULT_GOLDSET_PATH = Path("data/gold_posts/hormozi_master.json")


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


GOLDSET_MIN_SIMILARITY = float(os.getenv("UMBRAL_SIMILITUD", os.getenv("GOLDSET_MIN_SIMILITUD", "0.75")) or 0.75)
GOLDSET_EMBED_PATH = Path(os.getenv("GOLDSET_EMBED_PATH", "data/gold_posts/goldset_embeddings.npz"))
GOLDSET_COLLECTION_NAME_DEFAULT = os.getenv("GOLDSET_COLLECTION_NAME", "goldset_norm_v1")
EXPECTED_NORMALIZER_VERSION = int(os.getenv("GOLDSET_NORMALIZER_VERSION", "1") or 1)
GOLDSET_CLUSTER_COUNT = max(1, int(os.getenv("GOLDSET_CLUSTER_COUNT", "8") or 8))
GOLDSET_NPZ_URI = os.getenv("GOLDSET_NPZ_GCS_URI", "").strip()

_NPZ_CACHE_PATH: Optional[Path] = None

_ACTIVE_COLLECTION_NAME: str = GOLDSET_COLLECTION_NAME_DEFAULT
_GOLDSET_IDS: List[str] = []
_GOLDSET_TEXTS: List[str] = []
_GOLDSET_EMBEDDINGS: List[List[float]] = []
_GOLDSET_EMB_DIM: int = 0
_GOLDSET_NORMALIZER_VERSION: int = EXPECTED_NORMALIZER_VERSION
_GOLDSET_CLUSTER_INFO: Optional[List[Tuple[np.ndarray, str]]] = None
_GOLDSET_LOADED: bool = False
_GOLDSET_LOAD_ERROR: Optional[str] = None


def get_active_goldset_collection_name() -> str:
    return _ACTIVE_COLLECTION_NAME


def _emit_ready(collection_name: str, count: int, emb_dim: int, normalizer_version: int) -> None:
    payload = {
        "event": "DIAG_GOLDSET_READY",
        "collection_name": collection_name,
        "count": count,
        "emb_dim": emb_dim,
        "normalizer_version": normalizer_version,
    }
    emit_structured(payload)
    logger.info(
        "GOLDSET_READY name=%s count=%s emb_dim=%s normalizer_version=%s",
        collection_name,
        count,
        emb_dim,
        normalizer_version,
    )


def _emit_npz_loaded(collection_name: str, count: int, emb_dim: int, npz_uri: Optional[str]) -> None:
    payload = {
        "message": "GOLDSET_NPZ_LOADED",
        "goldset_collection": collection_name,
        "count": count,
        "emb_dim": emb_dim,
    }
    if npz_uri:
        payload["npz_uri"] = npz_uri
    emit_structured(payload)


def _emit_npz_failed(npz_uri: Optional[str], error: Exception) -> None:
    emit_structured(
        {
            "message": "GOLDSET_NPZ_LOAD_FAILED",
            "npz_uri": npz_uri,
            "error": str(error),
        }
    )
    logger.error("Failed to load goldset NPZ %s: %s", npz_uri or "<local>", error)


def _resolve_goldset_npz_path(npz_uri: Optional[str]) -> Path:
    if not npz_uri:
        return GOLDSET_EMBED_PATH

    global _NPZ_CACHE_PATH
    if _NPZ_CACHE_PATH and _NPZ_CACHE_PATH.exists():
        return _NPZ_CACHE_PATH

    if not npz_uri.startswith("gs://"):
        path = Path(npz_uri)
        _NPZ_CACHE_PATH = path
        return path

    try:
        from google.cloud import storage  # type: ignore
    except Exception as exc:
        raise RuntimeError(f"google.cloud.storage import failed for NPZ URI {npz_uri}: {exc}") from exc

    bucket_name, _, blob_name = npz_uri[5:].partition("/")
    if not bucket_name or not blob_name:
        raise ValueError(f"GOLDSET_NPZ_GCS_URI inválido: {npz_uri}")

    target_dir = Path("/tmp/goldset_npz")
    target_dir.mkdir(parents=True, exist_ok=True)
    local_path = target_dir / f"{bucket_name}_{blob_name.replace('/', '_')}"

    if local_path.exists():
        _NPZ_CACHE_PATH = local_path
        return local_path

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.download_to_filename(str(local_path))
    logger.info("Goldset NPZ descargado desde %s a %s.", npz_uri, local_path)
    _NPZ_CACHE_PATH = local_path
    return local_path


def _load_embeddings_from_npz(path: Path, *, npz_uri: Optional[str]) -> Tuple[List[str], List[str], List[List[float]], dict]:
    if not path.exists():
        raise FileNotFoundError(f"Goldset NPZ not found: {path}")

    try:
        data = np.load(path, allow_pickle=True)
    except Exception as exc:
        raise RuntimeError(f"Failed to read NPZ at {path}: {exc}") from exc

    texts = data.get("texts")
    documents = data.get("documents")
    embeddings = data.get("embeddings")
    vectors = data.get("vectors")
    ids_arr = data.get("ids")
    meta_entry = data.get("meta")

    texts_arr = texts if texts is not None else documents
    vectors_arr = embeddings if embeddings is not None else vectors
    if texts_arr is None or vectors_arr is None:
        raise RuntimeError(f"NPZ missing required fields (texts/embeddings): {path}")

    try:
        texts_list = texts_arr.tolist() if hasattr(texts_arr, "tolist") else list(texts_arr)
        decoded_texts = [t.decode("utf-8") if isinstance(t, bytes) else str(t) for t in texts_list]
        vectors_list_raw = vectors_arr.tolist() if hasattr(vectors_arr, "tolist") else list(vectors_arr)
        vectors_list: List[List[float]] = []
        for vec in vectors_list_raw:
            if hasattr(vec, "tolist"):
                vec = vec.tolist()
            vectors_list.append([float(x) for x in vec])

        if ids_arr is not None:
            ids_raw = ids_arr.tolist() if hasattr(ids_arr, "tolist") else list(ids_arr)
            decoded_ids = [i.decode("utf-8") if isinstance(i, bytes) else str(i) for i in ids_raw]
        else:
            decoded_ids = [f"npz_{idx:05d}" for idx in range(len(decoded_texts))]

        meta: dict = {}
        if meta_entry is not None:
            if hasattr(meta_entry, "tolist"):
                meta_entry = meta_entry.tolist()
            if isinstance(meta_entry, (bytes, bytearray)):
                meta_entry = meta_entry.decode("utf-8")
            if isinstance(meta_entry, str):
                meta = json.loads(meta_entry)
            elif isinstance(meta_entry, dict):
                meta = dict(meta_entry)
    except Exception as exc:
        raise RuntimeError(f"Failed to parse NPZ contents for {path}: {exc}") from exc

    return decoded_ids, decoded_texts, vectors_list, meta


def _collection_name_from_meta(meta: dict, default: str) -> str:
    for key in ("collection", "collection_name", "name"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return default


def _collection_name_from_path(path: Path, default: str) -> str:
    stem = path.stem if path else ""
    return stem or default


def _load_goldset() -> None:
    global _GOLDSET_LOADED, _GOLDSET_LOAD_ERROR, _ACTIVE_COLLECTION_NAME
    global _GOLDSET_IDS, _GOLDSET_TEXTS, _GOLDSET_EMBEDDINGS, _GOLDSET_EMB_DIM, _GOLDSET_NORMALIZER_VERSION, _GOLDSET_CLUSTER_INFO

    if _GOLDSET_LOADED:
        return
    if _GOLDSET_LOAD_ERROR:
        raise RuntimeError(_GOLDSET_LOAD_ERROR)

    npz_uri = GOLDSET_NPZ_URI or None
    npz_path = _resolve_goldset_npz_path(npz_uri)

    try:
        ids, texts, embeddings, meta = _load_embeddings_from_npz(npz_path, npz_uri=npz_uri)
    except FileNotFoundError as exc:
        # Fallback: cargar solo textos desde DEFAULT_GOLDSET_PATH para operaciones que no requieren embeddings
        try:
            fallback_texts = load_gold_texts(DEFAULT_GOLDSET_PATH)
        except Exception as inner_exc:
            _GOLDSET_LOAD_ERROR = f"Goldset NPZ missing and text fallback failed: {inner_exc}"
            _emit_npz_failed(npz_uri, exc)
            raise

        collection_default = _collection_name_from_path(npz_path, GOLDSET_COLLECTION_NAME_DEFAULT)
        _ACTIVE_COLLECTION_NAME = collection_default
        _GOLDSET_IDS[:] = [f"text_{i:05d}" for i in range(len(fallback_texts))]
        _GOLDSET_TEXTS[:] = list(fallback_texts)
        _GOLDSET_EMBEDDINGS[:] = []
        _GOLDSET_EMB_DIM = 0
        _GOLDSET_NORMALIZER_VERSION = EXPECTED_NORMALIZER_VERSION
        _GOLDSET_CLUSTER_INFO = None

        _emit_npz_failed(npz_uri, exc)
        _emit_ready(_ACTIVE_COLLECTION_NAME, len(_GOLDSET_TEXTS), _GOLDSET_EMB_DIM, _GOLDSET_NORMALIZER_VERSION)
        _GOLDSET_LOADED = True
        return
    except Exception as exc:
        _GOLDSET_LOAD_ERROR = str(exc)
        _emit_npz_failed(npz_uri, exc)
        raise

    if not embeddings:
        exc = RuntimeError("Goldset NPZ contains no embeddings.")
        _GOLDSET_LOAD_ERROR = str(exc)
        _emit_npz_failed(npz_uri, exc)
        raise exc

    collection_default = _collection_name_from_path(npz_path, GOLDSET_COLLECTION_NAME_DEFAULT)
    collection_name = _collection_name_from_meta(meta, collection_default)
    emb_dim = int(meta.get("emb_dim") or len(embeddings[0]))
    normalizer_version = int(meta.get("normalizer_version") or EXPECTED_NORMALIZER_VERSION)

    _ACTIVE_COLLECTION_NAME = collection_name
    _GOLDSET_IDS = ids
    _GOLDSET_TEXTS = texts
    _GOLDSET_EMBEDDINGS = embeddings
    _GOLDSET_EMB_DIM = emb_dim
    _GOLDSET_NORMALIZER_VERSION = normalizer_version
    _GOLDSET_CLUSTER_INFO = None

    _emit_npz_loaded(collection_name, len(texts), emb_dim, npz_uri)
    _emit_ready(collection_name, len(texts), emb_dim, normalizer_version)
    _GOLDSET_LOADED = True


@dataclass(frozen=True)
class GoldsetSimilarity:
    similarity: float
    similarity_raw: float
    similarity_norm: float
    best_id: str


def _ensure_goldset_loaded() -> None:
    if _GOLDSET_LOADED:
        return
    _load_goldset()


def get_goldset_similarity_details(text: str, *, generate_if_missing: bool = True) -> GoldsetSimilarity:
    _ensure_goldset_loaded()
    normalized = normalize_for_embedding(text or "")
    try:
        vec = get_embedding(normalized, generate_if_missing=generate_if_missing)
    except Exception as exc:
        emit_structured({"message": "GOLDSET_DRAFT_EMBED_FAIL", "error": str(exc)})
        return GoldsetSimilarity(0.0, 0.0, 0.0, "embedding_error")

    if not vec:
        emit_structured({"message": "GOLDSET_DRAFT_EMBED_EMPTY"})
        return GoldsetSimilarity(0.0, 0.0, 0.0, "embedding_missing")

    best_idx = -1
    best_score = float("-inf")
    for idx, gold_vec in enumerate(_GOLDSET_EMBEDDINGS):
        score = _cosine_similarity(vec, gold_vec)
        if score > best_score:
            best_score = score
            best_idx = idx

    if best_idx < 0:
        emit_structured({"message": "GOLDSET_NO_MATCH"})
        return GoldsetSimilarity(0.0, 0.0, 0.0, "no_match")

    similarity_raw = float(best_score)
    similarity_norm = max(0.0, min(1.0, similarity_raw))
    best_id = _GOLDSET_IDS[best_idx] if 0 <= best_idx < len(_GOLDSET_IDS) else "unknown"

    return GoldsetSimilarity(similarity_raw, similarity_raw, similarity_norm, best_id)


def max_similarity_to_goldset(text: str, *, generate_if_missing: bool = True) -> float:
    details = get_goldset_similarity_details(text, generate_if_missing=generate_if_missing)
    return details.similarity


def _normalize_vector(arr: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(arr)
    if norm == 0:
        return arr
    return arr / norm


def _build_cluster_anchors() -> List[Tuple[np.ndarray, str]]:
    _ensure_goldset_loaded()
    vectors = np.array([_normalize_vector(np.array(vec, dtype=float)) for vec in _GOLDSET_EMBEDDINGS])
    texts = _GOLDSET_TEXTS
    n_samples = vectors.shape[0]
    k = min(GOLDSET_CLUSTER_COUNT, n_samples)
    if k <= 0:
        return []

    rng = np.random.default_rng(42)
    initial_indices = rng.choice(n_samples, size=k, replace=False)
    centroids = vectors[initial_indices].copy()

    assignments = np.zeros(n_samples, dtype=int)
    for _ in range(30):
        similarities = vectors @ centroids.T
        new_assignments = np.argmax(similarities, axis=1)
        if np.array_equal(assignments, new_assignments):
            break
        assignments = new_assignments
        for idx in range(k):
            members = vectors[assignments == idx]
            if members.size == 0:
                centroids[idx] = vectors[rng.integers(0, n_samples)].copy()
            else:
                centroids[idx] = _normalize_vector(members.mean(axis=0))

    anchors: List[Tuple[np.ndarray, str]] = []
    for idx in range(k):
        members_idx = np.where(assignments == idx)[0]
        if members_idx.size == 0:
            continue
        centroid = centroids[idx]
        member_vectors = vectors[members_idx]
        sims = member_vectors @ centroid
        best_local = members_idx[int(np.argmax(sims))]
        anchors.append((centroid, texts[best_local]))
    return anchors


def _get_cluster_anchors() -> List[Tuple[np.ndarray, str]]:
    global _GOLDSET_CLUSTER_INFO
    if _GOLDSET_CLUSTER_INFO is None:
        _GOLDSET_CLUSTER_INFO = _build_cluster_anchors()
    return _GOLDSET_CLUSTER_INFO


def retrieve_goldset_examples(query: str, k: int = 3) -> List[str]:
    """Deprecated: use retrieve_goldset_examples_nn for full nearest-neighbor search.

    This function returns cluster anchor texts closest to the query.
    Kept for backward compatibility; prefer NN for higher-fidelity style RAG.
    """
    anchors = _get_cluster_anchors()
    if not anchors:
        return _GOLDSET_TEXTS[:k]

    vector = None
    if query and query.strip():
        try:
            vec = get_embedding(normalize_for_embedding(query), generate_if_missing=False)
            if vec:
                vector = _normalize_vector(np.array(vec, dtype=float))
        except Exception as exc:
            emit_structured({"message": "GOLDSET_QUERY_EMBED_FAIL", "error": str(exc)})
            vector = None

    if vector is None or vector.size == 0:
        return [text for _, text in anchors[:k]]

    anchor_dim = anchors[0][0].shape[0] if anchors else 0
    if vector.shape[0] != anchor_dim:
        emit_structured(
            {
                "message": "GOLDSET_QUERY_DIM_MISMATCH",
                "query_dim": vector.shape[0],
                "anchor_dim": anchor_dim,
            }
        )
        return [text for _, text in anchors[:k]]

    scored = [(float(vector @ centroid), text) for centroid, text in anchors]
    scored.sort(key=lambda item: item[0], reverse=True)
    return [text for _, text in scored[:k]]


def retrieve_goldset_examples_nn(query: str, k: int = 3, min_similarity: float = 0.0) -> List[str]:
    """Nearest-neighbor retrieval across ALL goldset embeddings.

    - Embeds the query using the same pipeline as similarity checks.
    - Computes cosine similarity against each goldset vector.
    - Returns top-k texts sorted by similarity, optionally filtering by a minimum similarity.
    - Falls back to cluster anchors if the query embedding is missing or dimension-mismatched.
    """
    _ensure_goldset_loaded()

    normalized = normalize_for_embedding(query or "")
    try:
        qvec = get_embedding(normalized, generate_if_missing=True)
    except Exception as exc:
        emit_structured({"message": "GOLDSET_QUERY_EMBED_FAIL", "error": str(exc)})
        qvec = []

    if not qvec:
        return retrieve_goldset_examples(query, k=k)

    try:
        qarr = np.array(qvec, dtype=float)
    except Exception:
        return retrieve_goldset_examples(query, k=k)

    if qarr.shape[0] != _GOLDSET_EMB_DIM:
        emit_structured(
            {
                "message": "GOLDSET_QUERY_DIM_MISMATCH",
                "query_dim": int(qarr.shape[0]),
                "emb_dim": int(_GOLDSET_EMB_DIM),
            }
        )
        return retrieve_goldset_examples(query, k=k)

    qnorm = _normalize_vector(qarr)
    # Compute cosine similarity using dot since both are normalized
    gold_vectors = [np.array(vec, dtype=float) for vec in _GOLDSET_EMBEDDINGS]
    gold_norms = [
        _normalize_vector(vec) if np.linalg.norm(vec) > 0 else vec for vec in gold_vectors
    ]
    scored: List[Tuple[float, str]] = []
    for vec, text in zip(gold_norms, _GOLDSET_TEXTS):
        sim = float(np.dot(qnorm, vec))
        if sim >= min_similarity:
            scored.append((sim, text))

    if not scored:
        return retrieve_goldset_examples(query, k=k)

    scored.sort(key=lambda item: item[0], reverse=True)
    return [text for _, text in scored[:k]]

