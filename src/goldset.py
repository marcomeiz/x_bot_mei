import json
import math
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from embeddings_manager import get_embedding, get_chroma_client
from diagnostics_logger import emit_structured
from logger_config import logger
from src.chroma_utils import flatten_chroma_array
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
GOLDSET_COLLECTION_NAME = os.getenv("GOLDSET_COLLECTION_NAME", "goldset_norm_v1")
EXPECTED_NORMALIZER_VERSION = int(os.getenv("GOLDSET_NORMALIZER_VERSION", "1") or 1)
GOLDSET_CLUSTER_COUNT = max(1, int(os.getenv("GOLDSET_CLUSTER_COUNT", "8") or 8))
GOLDSET_IDS_CACHE: Optional[List[str]] = None
GOLDSET_TEXTS_CACHE: Optional[List[str]] = None
GOLDSET_EMB_CACHE: Optional[List[Sequence[float]]] = None
GOLDSET_CLUSTER_INFO: Optional[List[Tuple[np.ndarray, str]]] = None  # (centroid, anchor_text)

_ACTIVE_GOLDSET_COLLECTION_NAME = GOLDSET_COLLECTION_NAME
_GOLDSET_LOGGED = False
_NPZ_CACHE_PATH: Optional[Path] = None


def _set_active_collection(name: Optional[str]) -> None:
    global _ACTIVE_GOLDSET_COLLECTION_NAME
    if name:
        _ACTIVE_GOLDSET_COLLECTION_NAME = name


def get_active_goldset_collection_name() -> str:
    return _ACTIVE_GOLDSET_COLLECTION_NAME


def _emit_goldset_ready(collection_name: str, count: int, emb_dim: int, normalizer_version: int) -> None:
    global _GOLDSET_LOGGED
    if _GOLDSET_LOGGED:
        return
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
    _GOLDSET_LOGGED = True


def _emit_goldset_error(reason: str, **context: object) -> None:
    payload = {"event": "DIAG_GOLDSET_ERROR", "reason": reason}
    payload.update({k: v for k, v in context.items() if v is not None})
    emit_structured(payload)
    logger.error("GOLDSET_ERROR %s %s", reason, context)


def _resolve_goldset_npz_path() -> Path:
    uri = os.getenv("GOLDSET_NPZ_GCS_URI", "").strip()
    if not uri:
        return GOLDSET_EMBED_PATH
    if not uri.startswith("gs://"):
        return Path(uri)

    global _NPZ_CACHE_PATH
    if _NPZ_CACHE_PATH and _NPZ_CACHE_PATH.exists():
        return _NPZ_CACHE_PATH

    try:
        from google.cloud import storage  # type: ignore
    except Exception as exc:
        _emit_goldset_error("storage_import_failed", uri=uri, error=str(exc))
        return GOLDSET_EMBED_PATH

    bucket_name, _, blob_name = uri[5:].partition("/")
    if not bucket_name or not blob_name:
        _emit_goldset_error("invalid_npz_uri", uri=uri)
        return GOLDSET_EMBED_PATH

    target_dir = Path("/tmp/goldset_npz")
    target_dir.mkdir(parents=True, exist_ok=True)
    local_path = target_dir / f"{bucket_name}_{blob_name.replace('/', '_')}"

    if local_path.exists():
        _NPZ_CACHE_PATH = local_path
        return local_path

    try:
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        blob.download_to_filename(str(local_path))
        logger.info("Goldset NPZ descargado desde %s a %s.", uri, local_path)
        _NPZ_CACHE_PATH = local_path
        return local_path
    except Exception as exc:
        _emit_goldset_error("npz_download_failed", uri=uri, error=str(exc))
        return GOLDSET_EMBED_PATH


def _load_embeddings_from_npz(path: Path, *, npz_uri: Optional[str] = None) -> Tuple[List[str], List[str], List[Sequence[float]], Dict[str, object]]:
    label = npz_uri or str(path)
    if not path.exists():
        _emit_goldset_error("npz_missing", path=str(path), uri=npz_uri)
        raise FileNotFoundError(f"Goldset NPZ not found: {path}")
    try:
        data = np.load(path, allow_pickle=True)
    except Exception as exc:
        _emit_goldset_error("npz_read_failed", path=str(path), uri=npz_uri, error=str(exc))
        raise RuntimeError(f"Failed to read NPZ at {label}") from exc
    texts = data.get("texts")
    documents = data.get("documents")
    embeddings = data.get("embeddings")
    vectors = data.get("vectors")
    ids_arr = data.get("ids")
    meta_entry = data.get("meta")
    texts_arr = texts if texts is not None else documents
    vectors_arr = embeddings if embeddings is not None else vectors
    if texts_arr is None or vectors_arr is None:
        _emit_goldset_error("npz_missing_fields", path=str(path), uri=npz_uri)
        raise RuntimeError(f"NPZ missing expected fields: {label}")
    try:
        texts_list = texts_arr.tolist() if hasattr(texts_arr, "tolist") else list(texts_arr)
        decoded_texts = [t.decode("utf-8") if isinstance(t, bytes) else str(t) for t in texts_list]
        vectors_list = vectors_arr.tolist() if hasattr(vectors_arr, "tolist") else list(vectors_arr)
        if ids_arr is not None:
            ids_raw = ids_arr.tolist() if hasattr(ids_arr, "tolist") else list(ids_arr)
            decoded_ids = [i.decode("utf-8") if isinstance(i, bytes) else str(i) for i in ids_raw]
        else:
            decoded_ids = [f"npz_{idx:05d}" for idx in range(len(decoded_texts))]
        meta: Dict[str, object] = {}
        if meta_entry is not None:
            try:
                if hasattr(meta_entry, "tolist"):
                    meta_entry = meta_entry.tolist()
                if isinstance(meta_entry, (bytes, bytearray)):
                    meta_entry = meta_entry.decode("utf-8")
                if isinstance(meta_entry, str):
                    meta = json.loads(meta_entry)
                elif isinstance(meta_entry, dict):
                    meta = dict(meta_entry)
            except Exception as exc_meta:
                _emit_goldset_error("npz_meta_parse_failed", path=str(path), uri=npz_uri, error=str(exc_meta))
    except Exception as exc:
        _emit_goldset_error("npz_parse_failed", path=str(path), uri=npz_uri, error=str(exc))
        raise RuntimeError(f"Failed to parse NPZ contents for {label}") from exc
    return decoded_ids, decoded_texts, vectors_list, meta


def _validate_normalizer_version(metadatas: Sequence) -> None:
    if not metadatas:
        return
    for meta in metadatas:
        if isinstance(meta, dict):
            version = meta.get("normalizer_version")
            if version is not None and int(version) != EXPECTED_NORMALIZER_VERSION:
                logger.warning(
                    "Goldset normalizer version mismatch (expected=%s, found=%s).",
                    EXPECTED_NORMALIZER_VERSION,
                    version,
                )
                break


def _load_embeddings_from_chroma() -> Optional[Tuple[List[str], List[str], List[Sequence[float]], Dict[str, object]]]:
    try:
        client = get_chroma_client()
        collection = client.get_or_create_collection(GOLDSET_COLLECTION_NAME, metadata={"hnsw:space": "cosine"})
        result = collection.get(include=["ids", "embeddings", "documents", "metadatas"]) or {}

        documents_raw = result.get("documents") or []
        embeddings = result.get("embeddings") or []
        ids_raw = result.get("ids") or []
        documents = flatten_chroma_array(documents_raw)
        ids = flatten_chroma_array(ids_raw)

        texts: List[str] = []
        for entry in documents:
            if isinstance(entry, list):
                texts.append(entry[0] if entry else "")
            else:
                texts.append(str(entry))
        ids = [str(item) for item in ids]

        if not texts or not embeddings:
            logger.info("Chroma goldset empty or missing embeddings.")
            return None

        collection_meta = collection.metadata or {}
        _emit_goldset_ready(
            collection_meta.get("collection", GOLDSET_COLLECTION_NAME) or GOLDSET_COLLECTION_NAME,
            len(texts),
            int(collection_meta.get("emb_dim") or (len(embeddings[0]) if embeddings else 0) or 0),
            int(collection_meta.get("normalizer_version") or EXPECTED_NORMALIZER_VERSION),
        )
        _validate_normalizer_version(result.get("metadatas") or [])
        return ids, texts, embeddings, collection_meta
    except Exception as exc:
        _emit_goldset_error("chroma_load_failed", error=str(exc))
        return None


def _derive_collection_name_from_meta(meta: Dict[str, object], fallback: str) -> str:
    if not meta:
        return fallback
    for key in ("collection", "collection_name", "name"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def _derive_collection_name_from_path(path: Path, fallback: str) -> str:
    name = path.stem if path else ""
    return name or fallback


def _get_gold_records() -> Tuple[List[str], List[str], List[Sequence[float]]]:
    global GOLDSET_IDS_CACHE, GOLDSET_TEXTS_CACHE, GOLDSET_EMB_CACHE
    if (
        GOLDSET_IDS_CACHE is not None
        and GOLDSET_TEXTS_CACHE is not None
        and GOLDSET_EMB_CACHE is not None
    ):
        return GOLDSET_IDS_CACHE, GOLDSET_TEXTS_CACHE, GOLDSET_EMB_CACHE

    env_uri = os.getenv("GOLDSET_NPZ_GCS_URI", "").strip()
    if env_uri:
        npz_path = _resolve_goldset_npz_path()
        try:
            ids, texts, embeddings, meta = _load_embeddings_from_npz(npz_path, npz_uri=env_uri)
        except Exception as exc:
            emit_structured(
                {
                    "message": "GOLDSET_NPZ_LOAD_FAILED",
                    "npz_uri": env_uri,
                    "error": str(exc),
                }
            )
            _emit_goldset_error("npz_load_failed", uri=env_uri, error=str(exc))
            raise
        GOLDSET_IDS_CACHE, GOLDSET_TEXTS_CACHE, GOLDSET_EMB_CACHE = ids, texts, embeddings
        collection_name = _derive_collection_name_from_meta(meta, _derive_collection_name_from_path(npz_path, GOLDSET_COLLECTION_NAME))
        _set_active_collection(collection_name)
        emb_dim = int(meta.get("emb_dim") or (len(embeddings[0]) if embeddings else 0) or 0) if meta else (len(embeddings[0]) if embeddings else 0)
        normalizer_version = int(meta.get("normalizer_version") or EXPECTED_NORMALIZER_VERSION) if meta else EXPECTED_NORMALIZER_VERSION
        emit_structured(
            {
                "message": "GOLDSET_NPZ_LOADED",
                "goldset_collection": collection_name,
                "count": len(texts),
                "emb_dim": emb_dim,
                "npz_uri": env_uri,
            }
        )
        _emit_goldset_ready(collection_name, len(texts), emb_dim, normalizer_version)
        logger.info("Goldset embeddings loaded from NPZ (%s).", npz_path)
        return ids, texts, embeddings

    chroma_data = _load_embeddings_from_chroma()
    if chroma_data:
        ids, texts, embeddings, meta = chroma_data
        GOLDSET_IDS_CACHE, GOLDSET_TEXTS_CACHE, GOLDSET_EMB_CACHE = ids, texts, embeddings
        collection_name = _derive_collection_name_from_meta(meta, GOLDSET_COLLECTION_NAME)
        _set_active_collection(collection_name)
        emb_dim = int(meta.get("emb_dim") or (len(embeddings[0]) if embeddings else 0) or 0)
        normalizer_version = int(meta.get("normalizer_version") or EXPECTED_NORMALIZER_VERSION)
        _emit_goldset_ready(collection_name, len(texts), emb_dim, normalizer_version)
        logger.info("Goldset embeddings loaded from Chroma collection '%s'.", collection_name)
        return ids, texts, embeddings

    npz_path = _resolve_goldset_npz_path()
    try:
        npz_data = _load_embeddings_from_npz(npz_path)
    except Exception as exc:
        _emit_goldset_error("npz_load_failed", path=str(npz_path), error=str(exc))
        npz_data = None
    if npz_data:
        ids, texts, embeddings, meta = npz_data
        GOLDSET_IDS_CACHE, GOLDSET_TEXTS_CACHE, GOLDSET_EMB_CACHE = ids, texts, embeddings
        collection_name = _derive_collection_name_from_meta(meta, _derive_collection_name_from_path(npz_path, GOLDSET_COLLECTION_NAME))
        _set_active_collection(collection_name)
        emb_dim = int(meta.get("emb_dim") or (len(embeddings[0]) if embeddings else 0) or 0) if meta else (len(embeddings[0]) if embeddings else 0)
        normalizer_version = int(meta.get("normalizer_version") or EXPECTED_NORMALIZER_VERSION) if meta else EXPECTED_NORMALIZER_VERSION
        _emit_goldset_ready(collection_name, len(texts), emb_dim, normalizer_version)
        logger.info("Goldset embeddings loaded from NPZ (%s).", npz_path)
        return ids, texts, embeddings

    allow_runtime = os.getenv("GOLDSET_ALLOW_RUNTIME_COMPUTE", "0").lower() in {"1", "true", "yes", "y"}
    if allow_runtime:
        ids, texts, embeddings = _compute_embeddings_locally()
        GOLDSET_IDS_CACHE, GOLDSET_TEXTS_CACHE, GOLDSET_EMB_CACHE = ids, texts, embeddings
        collection_name = "runtime_goldset"
        _set_active_collection(collection_name)
        emb_dim = len(embeddings[0]) if embeddings else 0
        _emit_goldset_ready(collection_name, len(texts), emb_dim, EXPECTED_NORMALIZER_VERSION)
        logger.warning("Goldset embeddings computed on the fly. Consider precomputing for stability.")
        return ids, texts, embeddings

    _emit_goldset_error("goldset_unavailable")
    GOLDSET_IDS_CACHE, GOLDSET_TEXTS_CACHE, GOLDSET_EMB_CACHE = [], [], []
    return GOLDSET_IDS_CACHE, GOLDSET_TEXTS_CACHE, GOLDSET_EMB_CACHE


def _get_gold_embeddings() -> Tuple[List[str], List[Sequence[float]]]:
    _, texts, embeddings = _get_gold_records()
    return texts, embeddings


def _compute_embeddings_locally() -> Tuple[List[str], List[str], List[Sequence[float]]]:
    texts = load_gold_texts()
    embeddings: List[Sequence[float]] = []
    valid_texts: List[str] = []
    ids: List[str] = []
    for idx, text in enumerate(texts):
        try:
            vec = get_embedding(normalize_for_embedding(text))
        except Exception as exc:
            logger.warning("Goldset embedding failed: %s", exc)
            continue
        if vec:
            embeddings.append(vec)
            valid_texts.append(text)
            ids.append(f"runtime_{idx:05d}")
    if not embeddings:
        logger.error("No embeddings computed for gold set; similarity checks will fallback to 0.")
    return ids, valid_texts, embeddings


@dataclass(frozen=True)
class GoldsetSimilarity:
    similarity: Optional[float]
    similarity_raw: Optional[float]
    similarity_norm: Optional[float]
    best_id: Optional[str]


def get_goldset_similarity_details(text: str, *, generate_if_missing: bool = False) -> GoldsetSimilarity:
    if not text or not text.strip():
        return GoldsetSimilarity(None, None, None, None)
    try:
        vec = get_embedding(normalize_for_embedding(text), generate_if_missing=generate_if_missing)
    except Exception as exc:  # pragma: no cover
        _emit_goldset_error("draft_embedding_failed", error=str(exc))
        return GoldsetSimilarity(None, None, None, None)
    if not vec:
        _emit_goldset_error("draft_embedding_missing")
        return GoldsetSimilarity(None, None, None, None)

    ids, _, gold_embeddings = _get_gold_records()
    if not gold_embeddings:
        _emit_goldset_error("goldset_embeddings_empty")
        return GoldsetSimilarity(None, None, None, None)

    best_idx = -1
    best_score = float("-inf")
    for idx, gold_vec in enumerate(gold_embeddings):
        score = _cosine_similarity(vec, gold_vec)
        if score > best_score:
            best_score = score
            best_idx = idx

    if best_idx < 0:
        _emit_goldset_error("goldset_no_match")
        return GoldsetSimilarity(None, None, None, None)

    similarity_raw = float(best_score)
    similarity_norm = max(0.0, min(1.0, similarity_raw))
    best_id = ids[best_idx] if 0 <= best_idx < len(ids) else None

    return GoldsetSimilarity(
        similarity=similarity_raw,
        similarity_raw=similarity_raw,
        similarity_norm=similarity_norm,
        best_id=best_id,
    )


def max_similarity_to_goldset(text: str, *, generate_if_missing: bool = False) -> Optional[float]:
    details = get_goldset_similarity_details(text, generate_if_missing=generate_if_missing)
    return details.similarity


def _normalize_vector(arr: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(arr)
    if norm == 0:
        return arr
    return arr / norm


def _build_cluster_anchors() -> List[Tuple[np.ndarray, str]]:
    global GOLDSET_CLUSTER_INFO
    texts, embeddings = _get_gold_embeddings()
    if not texts or not embeddings:
        GOLDSET_CLUSTER_INFO = []
        return GOLDSET_CLUSTER_INFO

    vectors = np.array([_normalize_vector(np.array(vec, dtype=float)) for vec in embeddings])
    n_samples = vectors.shape[0]
    k = min(GOLDSET_CLUSTER_COUNT, n_samples)
    if k <= 0:
        GOLDSET_CLUSTER_INFO = []
        return GOLDSET_CLUSTER_INFO

    rng = np.random.default_rng(42)
    initial_indices = rng.choice(n_samples, size=k, replace=False)
    centroids = vectors[initial_indices].copy()

    assignments = np.zeros(n_samples, dtype=int)
    for _ in range(30):
        similarities = vectors @ centroids.T  # cosine similarity because vectors normalized
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

    GOLDSET_CLUSTER_INFO = anchors
    return GOLDSET_CLUSTER_INFO


def _get_cluster_anchors() -> List[Tuple[np.ndarray, str]]:
    global GOLDSET_CLUSTER_INFO
    if GOLDSET_CLUSTER_INFO is not None:
        return GOLDSET_CLUSTER_INFO
    return _build_cluster_anchors()


def retrieve_goldset_examples(query: str, k: int = 3) -> List[str]:
    """Return up to k reference posts from goldset aligned with query topic."""
    anchors = _get_cluster_anchors()
    if not anchors:
        texts, _ = _get_gold_embeddings()
        return texts[:k]

    vector = None
    if query and query.strip():
        try:
            # Evitar generación en ruta /g; si no hay caché, se usarán anchors por defecto
            vector = np.array(get_embedding(query, generate_if_missing=False), dtype=float)
            vector = _normalize_vector(vector)
        except Exception as exc:
            logger.warning("Could not embed query for goldset retrieval: %s", exc)
            vector = None

    if vector is None or vector.size == 0:
        return [text for _, text in anchors[:k]]

    anchor_dim = anchors[0][0].shape[0] if anchors else 0
    if vector.shape[0] != anchor_dim:
        logger.warning(
            "Goldset anchor dimension mismatch (query=%s, anchor=%s). Falling back to raw texts.",
            vector.shape[0],
            anchor_dim,
        )
        return [text for _, text in anchors[:k]]

    scored = [
        (float(vector @ centroid), text)
        for centroid, text in anchors
    ]
    scored.sort(key=lambda item: item[0], reverse=True)
    return [text for _, text in scored[:k]]
