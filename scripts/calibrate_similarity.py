"""
Calibración de umbral de similitud (coseno) sobre una colección de Chroma.

Uso:
    CHROMA_DB_URL=http://<host>:<port> \
    EMBED_MODEL=openai/text-embedding-3-large \
    python scripts/calibrate_similarity.py --collection goldset_collection --thres-init 0.75

Opciones:
    --collection NOMBRE   Nombre de la colección a analizar (por defecto: goldset_collection)
    --thres-init VALOR   Umbral inicial sugerido (por defecto: 0.75)
    --samples N          Número de pares aleatorios para negativos (por defecto: 10000 o todos si <10000)

Salida:
    - Estadísticos de distribución de cosenos (positivos: vecinos más cercanos, negativos: pares aleatorios)
    - Sugerencia de UMBRAL_SIMILITUD inicial y cómo parametrizarlo por ENV.
"""

import argparse
import math
import os
import sys
from pathlib import Path
from typing import List, Sequence, Tuple

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

import numpy as np  # noqa: E402
from embeddings_manager import get_chroma_client  # noqa: E402
from logger_config import logger  # noqa: E402


def _normalize(arr: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(arr)
    if norm == 0:
        return arr
    return arr / norm


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _nearest_neighbor_cosines(vectors: List[Sequence[float]]) -> List[float]:
    if not vectors:
        return []
    mat = np.array([_normalize(np.array(v, dtype=float)) for v in vectors])
    sims: List[float] = []
    # Para cada vector, tomamos el NN excluyendo a sí mismo
    for i in range(mat.shape[0]):
        v = mat[i]
        # producto punto con todos (coseno porque normalizado)
        scores = mat @ v
        # excluir self
        scores[i] = -1.0
        best = float(np.max(scores))
        sims.append(best)
    return sims


def _random_pair_cosines(vectors: List[Sequence[float]], samples: int) -> List[float]:
    n = len(vectors)
    if n < 2:
        return []
    rng = np.random.default_rng(42)
    sims: List[float] = []
    for _ in range(min(samples, n * (n - 1) // 2)):
        i, j = rng.integers(0, n), rng.integers(0, n)
        if i == j:
            continue
        sims.append(_cosine(vectors[i], vectors[j]))
    return sims


def _summary(values: List[float]) -> dict:
    if not values:
        return {"count": 0}
    arr = np.array(values, dtype=float)
    return {
        "count": int(arr.size),
        "mean": float(np.mean(arr)),
        "std": float(np.std(arr)),
        "p10": float(np.percentile(arr, 10)),
        "p25": float(np.percentile(arr, 25)),
        "p50": float(np.percentile(arr, 50)),
        "p75": float(np.percentile(arr, 75)),
        "p90": float(np.percentile(arr, 90)),
        "min": float(np.min(arr)),
        "max": float(np.max(arr)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Calibración de umbral de similitud (coseno)")
    parser.add_argument("--collection", default=os.getenv("GOLDSET_COLLECTION_NAME", "goldset_norm_v1"))
    parser.add_argument("--samples", type=int, default=10000)
    parser.add_argument("--thres-init", type=float, default=0.75)
    args = parser.parse_args()

    client = get_chroma_client()
    coll = client.get_or_create_collection(name=args.collection, metadata={"hnsw:space": "cosine"})
    # En clientes HTTP v2, 'ids' no es parte de include
    data = coll.get(include=["embeddings", "documents"]) or {}
    vecs = data.get("embeddings") or []
    docs = data.get("documents") or []
    if not vecs:
        logger.error("La colección '%s' no contiene embeddings.", args.collection)
        return
    logger.info("Colección '%s': %s elementos.", args.collection, len(vecs))

    pos = _nearest_neighbor_cosines(vecs)
    neg = _random_pair_cosines(vecs, samples=args.samples)

    s_pos = _summary(pos)
    s_neg = _summary(neg)

    print("\n=== Distribuciones de coseno ===")
    print(f"Positivos (NN): {s_pos}")
    print(f"Negativos (random): {s_neg}")

    th = args.thres_init
    print("\n=== Sugerencia de umbral ===")
    print(f"UMBRAL_SIMILITUD inicial = {th}")
    print("Parametrizable por ENV: export UMBRAL_SIMILITUD=0.75 (o valor deseado)")

    # Heurística alternativa: si p25 de positivos < th, sugiere ajustarlo a p25
    try:
        suggested = max(0.0, min(1.0, float(s_pos.get("p25", th))))
        if suggested and suggested < th:
            print(f"Sugerencia heurística: usar p25 de positivos = {suggested:.3f}")
    except Exception:
        pass


if __name__ == "__main__":
    main()
