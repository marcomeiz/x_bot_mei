"""
Generate precomputed embeddings for the gold posts and save them to an .npz file.

Usage:
    python scripts/gen_goldset_npz.py [--output data/gold_posts/goldset_embeddings.npz]

Environment:
    GOLDSET_DATA_PATH (optional) default: data/gold_posts/hormozi_master.json
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import List

import numpy as np

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

from logger_config import logger  # noqa: E402

USE_LOCAL = os.getenv("LOCAL_EMBED", "0").lower() in {"1", "true", "yes"}
if USE_LOCAL:
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
        _local_model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
        def get_embedding(text: str):
            return _local_model.encode([text], normalize_embeddings=True)[0].tolist()
        logger.info("Usando embeddings locales con sentence-transformers/all-MiniLM-L6-v2")
    except Exception as e:
        logger.error(f"Fallo cargando modelo local: {e}")
        USE_LOCAL = False

if not USE_LOCAL:
    from embeddings_manager import get_embedding  # noqa: E402


GOLDSET_FILE = Path(os.getenv("GOLDSET_DATA_PATH", "data/gold_posts/hormozi_master.json"))
DEFAULT_OUTPUT = Path("data/gold_posts/goldset_embeddings.npz")
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


def generate_npz(output_path: Path) -> None:
    posts = load_gold_posts(GOLDSET_FILE)
    ids = []
    vectors = []
    kept = 0
    for idx, text in enumerate(posts, start=1):
        vec = get_embedding(text)
        if vec and isinstance(vec, (list, tuple)) and len(vec) > 0:
            ids.append(f"{ID_PREFIX}{idx:04d}")
            vectors.append(vec)
            kept += 1
        else:
            logger.warning("Skipping post without embedding (index=%s)", idx)

    if not vectors:
        logger.error("No embeddings generated; aborting.")
        return

    arr = np.array(vectors, dtype=np.float32)
    np.savez(output_path, ids=np.array(ids, dtype=object), vectors=arr)
    logger.info("Saved %s embeddings to %s", kept, output_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate .npz with precomputed goldset embeddings")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output .npz path")
    args = parser.parse_args()

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    generate_npz(out)


if __name__ == "__main__":
    main()