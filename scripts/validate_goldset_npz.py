#!/usr/bin/env python3
"""
Valida un NPZ del goldset asegurando que cumple el contrato:
- Arrays requeridos: ids, texts, embeddings (N, dim)
- Dimensión esperada (e.g., 3072)
- Conteo mínimo
- Sin NaN/Inf, sin IDs duplicados
- Metadatos opcionales: collection, emb_model, emb_dim, normalizer_version

Uso:
    python scripts/validate_goldset_npz.py --npz /ruta/al/goldset_norm_v1.npz \
        --expect-dim 3072 --min-count 100
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import numpy as np


EXPECTED_NORMALIZER_VERSION = int(os.getenv("GOLDSET_NORMALIZER_VERSION", "1") or 1)


def _fail(message: str) -> None:
    print(f"[ERROR] {message}", file=sys.stderr)
    sys.exit(1)


def _load_npz(path: Path) -> Dict[str, Any]:
    if not path.exists():
        _fail(f"NPZ no encontrado en {path}")
    try:
        data = np.load(path, allow_pickle=True)
    except Exception as exc:
        _fail(f"No se pudo leer NPZ ({path}): {exc}")
    return {key: data[key] for key in data.files}


def _as_list(value: Any) -> List[Any]:
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
        return list(value)
    return [value]


def _require_array(payload: Dict[str, Any], keys: Tuple[str, ...]) -> Tuple[List[Any], str]:
    for key in keys:
        if key in payload:
            return _as_list(payload[key]), key
    keys_str = ", ".join(keys)
    _fail(f"El NPZ debe contener alguno de los arrays: {keys_str}")


def _check_nan_inf(matrix: np.ndarray) -> None:
    if not np.isfinite(matrix).all():
        nan_count = np.isnan(matrix).sum()
        inf_count = np.isinf(matrix).sum()
        _fail(f"Embeddings contienen valores no finitos (nan={nan_count}, inf={inf_count})")


def _parse_meta(meta_raw: Any) -> Dict[str, Any]:
    if meta_raw is None:
        return {}
    if hasattr(meta_raw, "tolist"):
        meta_raw = meta_raw.tolist()
    if isinstance(meta_raw, (bytes, bytearray)):
        meta_raw = meta_raw.decode("utf-8")
    if isinstance(meta_raw, str):
        try:
            return json.loads(meta_raw)
        except json.JSONDecodeError:
            return {}
    if isinstance(meta_raw, dict):
        return dict(meta_raw)
    return {}


def validate_npz(npz_path: Path, expect_dim: int, min_count: int) -> None:
    payload = _load_npz(npz_path)

    ids, ids_key = _require_array(payload, ("ids", "piece_ids"))
    texts, texts_key = _require_array(payload, ("texts", "documents"))
    embeddings_raw, embeddings_key = _require_array(payload, ("embeddings", "vectors"))

    ids = [str(x).strip() for x in ids]
    texts = [str(x) for x in texts]
    embeddings = np.array(embeddings_raw, dtype=np.float32)

    if len(ids) != len(texts) or len(texts) != embeddings.shape[0]:
        _fail(
            f"Dimensiones inconsistentes: len(ids)={len(ids)} ({ids_key}), "
            f"len(texts)={len(texts)} ({texts_key}), embeddings={embeddings.shape}"
        )

    if len(ids) < min_count:
        _fail(f"El NPZ contiene {len(ids)} filas (< {min_count} requerido)")

    if expect_dim and embeddings.shape[1] != expect_dim:
        _fail(f"Diferencia de dimensión: expected {expect_dim}, got {embeddings.shape[1]}")

    duplicated = len(ids) != len(set(ids))
    if duplicated:
        _fail("IDs duplicados detectados en el NPZ")

    _check_nan_inf(embeddings)

    empty_texts = [i for i, txt in enumerate(texts, start=1) if not txt.strip()]
    if empty_texts:
        _fail(f"Textos vacíos en posiciones: {empty_texts[:5]}{'...' if len(empty_texts) > 5 else ''}")

    meta = _parse_meta(payload.get("meta"))
    emb_dim_meta = int(meta.get("emb_dim") or embeddings.shape[1])
    if emb_dim_meta != embeddings.shape[1]:
        _fail(f"meta.emb_dim={emb_dim_meta} pero embeddings tienen dimensión {embeddings.shape[1]}")

    normalizer_version = int(meta.get("normalizer_version") or EXPECTED_NORMALIZER_VERSION)
    collection = meta.get("collection") or "unknown"
    emb_model = meta.get("emb_model") or "unknown"

    print("[OK] Validación exitosa")
    print(f"  - collection: {collection}")
    print(f"  - emb_model: {emb_model}")
    print(f"  - normalizer_version: {normalizer_version}")
    print(f"  - filas: {len(ids)}")
    print(f"  - dimensión: {embeddings.shape[1]}")
    print(f"  - ids array: {ids_key}")
    print(f"  - texts array: {texts_key}")
    print(f"  - embeddings array: {embeddings_key}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Valida el contrato del NPZ del goldset")
    parser.add_argument("--npz", required=True, help="Ruta al NPZ a validar")
    parser.add_argument("--expect-dim", type=int, default=3072, help="Dimensión esperada de embeddings")
    parser.add_argument("--min-count", type=int, default=100, help="Mínimo de filas esperado")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    validate_npz(Path(args.npz), expect_dim=args.expect_dim, min_count=args.min_count)


if __name__ == "__main__":
    main()
