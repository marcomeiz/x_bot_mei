#!/usr/bin/env python3
"""
Regenera la colección `goldset_norm_v1` usando la normalización unificada antes de embeddar.
Requiere OPENROUTER_API_KEY válido (o proveedor equivalente configurado en embeddings_manager).
"""

import os
from datetime import datetime, timezone
from typing import List

from embeddings_manager import get_chroma_client, get_embedding
from src.goldset import load_gold_texts
from src.normalization import normalize_for_embedding

COLLECTION_NAME = os.getenv("GOLDSET_COLLECTION_NAME", "goldset_norm_v1")
EMB_MODEL = os.getenv("EMBED_MODEL", "openai/text-embedding-3-large")
EMB_DIM = int(os.getenv("SIM_DIM", "3072") or 3072)
NORMALIZER_VERSION = int(os.getenv("GOLDSET_NORMALIZER_VERSION", "1") or 1)

def main() -> None:
    os.environ.setdefault("ALLOW_RESET", "TRUE")

    client = get_chroma_client()

    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass

    collection = client.get_or_create_collection(
        COLLECTION_NAME,
        metadata={
            "hnsw:space": "cosine",
            "emb_model": EMB_MODEL,
            "emb_dim": EMB_DIM,
            "normalizer_version": NORMALIZER_VERSION,
        },
    )

    texts: List[str] = load_gold_texts()
    norm_texts = [normalize_for_embedding(t) for t in texts]

    ids = []
    embeddings = []
    metadatas = []
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    for idx, (raw, norm) in enumerate(zip(texts, norm_texts)):
        vec = get_embedding(norm, force=True)
        if not vec:
            raise RuntimeError(f"Embedding failed for goldset item {idx}")
        ids.append(f"gold_norm_{idx:05d}")
        embeddings.append(vec)
        metadatas.append({
            "original": raw,
            "normalizer_version": NORMALIZER_VERSION,
            "emb_model": EMB_MODEL,
            "emb_dim": EMB_DIM,
            "created_at": now_iso,
        })

    collection.add(ids=ids, documents=norm_texts, embeddings=embeddings, metadatas=metadatas)
    count = collection.count() or 0
    print(f"goldset_norm_v1 created with {count} items")

if __name__ == "__main__":
    main()
