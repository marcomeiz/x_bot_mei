"""
Upsert precomputed goldset embeddings from an .npz file into Chroma.

Usage:
    python scripts/upsert_goldset_from_npz.py
"""

import os
import sys
import numpy as np
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

from embeddings_manager import get_chroma_client  # noqa: E402
from logger_config import logger  # noqa: E402

NPZ_PATH = Path(os.getenv("GOLDSET_NPZ_PATH", "data/gold_posts/goldset_embeddings.npz"))
COLLECTION_NAME = os.getenv("GOLDSET_COLLECTION_NAME", "goldset_norm_v1")


def upsert_from_npz(npz_path: Path) -> None:
    if not npz_path.exists():
        raise FileNotFoundError(f"NPZ file not found at {npz_path}")

    data = np.load(npz_path, allow_pickle=True)
    ids = data.get("ids")
    vectors = data.get("vectors")
    documents = data.get("documents")

    if ids is None or vectors is None:
        raise ValueError("NPZ must contain 'ids' and 'vectors'.")
    if documents is None:
        # Fallback: if no documents provided, use empty string placeholders.
        documents = np.array([""] * len(ids), dtype=object)

    ids = [str(x) for x in ids.tolist()]
    embeddings = vectors.tolist()
    docs = [str(x) for x in documents.tolist()]

    client = get_chroma_client()
    collection = client.get_or_create_collection(COLLECTION_NAME, metadata={"hnsw:space": "cosine"})
    collection.upsert(ids=ids, embeddings=embeddings, documents=docs)
    logger.info("Goldset upserted: %s vectors into collection '%s'.", len(ids), COLLECTION_NAME)


if __name__ == "__main__":
    upsert_from_npz(NPZ_PATH)
