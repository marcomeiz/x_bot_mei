#!/usr/bin/env python3
"""
Auditoría forense del goldset mediante UMAP + HDBSCAN.

Entradas esperadas (NPZ):
- texts (o documents): lista/array de textos.
- embeddings (o vectors): lista/array de vectores (shape: N x D, p.ej. 149 x 3072).
- ids (opcional): lista/array de IDs (p.ej. H13, H71, ...). Si no existe, se sintetiza.
- meta (opcional): dict con metadatos.

Salidas:
- goldset_audit.png: visualización estática 2D coloreada por cluster.
- goldset_audit.json: resumen de clusters, tamaños y porcentaje de ruido.
- goldset_v2_audited.npz: NPZ filtrado con solo el cluster principal.

Uso:
  python scripts/audit_goldset_umap_hdbscan.py --npz data/gold_posts/goldset_norm_v1.npz \
         --out-dir data/gold_posts \
         [--neighbors 10] [--min-cluster-size 5] [--min-samples 1] [--random-state 42] \
         [--upload-uri gs://bucket/path/goldset_v2_audited.npz]

Notas:
- Métrica UMAP: cosine (CRÍTICO para embeddings).
- HDBSCAN en 2D usa euclidean.
- Si --upload-uri se pasa, intenta subir a GCS usando google.cloud.storage.
"""

import argparse
import json
import os
from pathlib import Path
from typing import List, Tuple, Dict

import numpy as np

def _load_npz(npz_path: Path) -> Tuple[List[str], List[str], List[List[float]], Dict]:
    if not npz_path.exists():
        raise FileNotFoundError(f"NPZ no encontrado: {npz_path}")
    data = np.load(npz_path, allow_pickle=True)
    texts = data.get("texts")
    documents = data.get("documents")
    embeddings = data.get("embeddings")
    vectors = data.get("vectors")
    ids_arr = data.get("ids")
    meta_entry = data.get("meta")

    texts_arr = texts if texts is not None else documents
    vectors_arr = embeddings if embeddings is not None else vectors
    if texts_arr is None or vectors_arr is None:
        raise RuntimeError(f"NPZ incompleto: faltan texts/embeddings en {npz_path}")

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

    meta: Dict = {}
    if meta_entry is not None:
        if hasattr(meta_entry, "tolist"):
            meta_entry = meta_entry.tolist()
        if isinstance(meta_entry, (bytes, bytearray)):
            meta_entry = meta_entry.decode("utf-8")
        if isinstance(meta_entry, str):
            try:
                meta = json.loads(meta_entry)
            except Exception:
                meta = {"raw_meta_str": meta_entry}
        elif isinstance(meta_entry, dict):
            meta = dict(meta_entry)

    return decoded_ids, decoded_texts, vectors_list, meta


def _upload_to_gcs(local_path: Path, upload_uri: str) -> None:
    try:
        from google.cloud import storage
    except Exception as e:
        print(f"[WARN] No se pudo importar google.cloud.storage: {e}. Omite upload.")
        return
    if not upload_uri.startswith("gs://"):
        print(f"[WARN] URI inválida para GCS: {upload_uri}. Debe empezar con gs://")
        return
    # Parse bucket and blob path
    tmp = upload_uri[5:].split("/", 1)
    if len(tmp) != 2:
        print(f"[WARN] URI de GCS incompleta: {upload_uri}")
        return
    bucket_name, blob_path = tmp[0], tmp[1]
    client = storage.Client(project=os.getenv("GCP_PROJECT_ID"))
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_path)
    blob.upload_from_filename(str(local_path))
    print(f"[GCS] Subido a {upload_uri}")


def main():
    parser = argparse.ArgumentParser(description="Auditoría UMAP+HDBSCAN del goldset")
    parser.add_argument("--npz", required=True, help="Ruta al NPZ (p.ej. data/gold_posts/goldset_norm_v1.npz)")
    parser.add_argument("--out-dir", default="data/gold_posts", help="Directorio de salida")
    parser.add_argument("--neighbors", type=int, default=10, help="UMAP n_neighbors")
    parser.add_argument("--min-dist", type=float, default=0.1, help="UMAP min_dist")
    parser.add_argument("--random-state", type=int, default=42, help="UMAP random_state")
    parser.add_argument("--min-cluster-size", type=int, default=5, help="HDBSCAN min_cluster_size")
    parser.add_argument("--min-samples", type=int, default=1, help="HDBSCAN min_samples")
    parser.add_argument("--upload-uri", type=str, default="", help="gs://bucket/path para subir el NPZ auditado")
    args = parser.parse_args()

    npz_path = Path(args.npz)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    ids, texts, vectors, meta = _load_npz(npz_path)
    X = np.array(vectors, dtype=float)
    n, d = X.shape[0], X.shape[1] if X.ndim == 2 else (len(X), len(X[0]) if X else 0)
    print(f"Cargado NPZ: N={n}, D={d}")

    # UMAP
    print("Iniciando reducción UMAP…")
    try:
        import umap
    except Exception:
        print("[ERROR] Falta dependencia: umap-learn. Instálala antes de continuar.")
        return
    reducer = umap.UMAP(
        n_neighbors=int(args.neighbors),
        n_components=2,
        min_dist=float(args.min_dist),
        metric="cosine",
        random_state=int(args.random_state),
    )
    embedding_2d = reducer.fit_transform(X)
    print(f"Reducción completa. Shape: {embedding_2d.shape}")

    # HDBSCAN
    print("Iniciando clusterización HDBSCAN…")
    try:
        import hdbscan
    except Exception:
        print("[ERROR] Falta dependencia: hdbscan. Instálala antes de continuar.")
        return
    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=int(args.min_cluster_size),
        min_samples=int(args.min_samples),
        metric="euclidean",
        cluster_selection_method="eom",
    )
    labels = clusterer.fit_predict(embedding_2d)

    # Visualización
    print("Generando visualización…")
    try:
        import matplotlib.pyplot as plt
    except Exception:
        print("[WARN] matplotlib no disponible, se omite plot.")
        plt = None
    if plt is not None:
        plt.figure(figsize=(8,6))
        plt.scatter(embedding_2d[:, 0], embedding_2d[:, 1], c=labels, cmap="Spectral", s=12)
        plt.title(f"Auditoría de Goldset: {len(np.unique(labels))} clusters (incluye ruido)")
        png_path = out_dir / "goldset_audit.png"
        plt.savefig(png_path, dpi=150)
        print(f"Plot guardado: {png_path}")

    # Reporte
    unique = [int(x) for x in np.unique(labels).tolist()]
    counts: Dict[int, int] = {lab: int(np.sum(labels == lab)) for lab in unique}
    noise_count = counts.get(-1, 0)
    noise_pct = round(100.0 * noise_count / len(labels), 2) if len(labels) else 0.0
    clusters_only = {lab: c for lab, c in counts.items() if lab >= 0}
    main_cluster_id = max(clusters_only, key=lambda k: clusters_only[k]) if clusters_only else None

    report = {
        "n_points": int(len(labels)),
        "n_clusters_including_noise": int(len(unique)),
        "counts_per_label": counts,
        "noise_count": int(noise_count),
        "noise_pct": float(noise_pct),
        "main_cluster_id": int(main_cluster_id) if main_cluster_id is not None else None,
    }
    json_path = out_dir / "goldset_audit.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"Reporte guardado: {json_path}")

    # Purga: filtrar por cluster principal
    if main_cluster_id is None:
        print("[WARN] No se identificaron clusters no-ruido. No se genera NPZ auditado.")
        return
    indices_buenos = np.where(labels == main_cluster_id)[0]
    X_limpio = X[indices_buenos]
    ids_limpios = [ids[i] for i in indices_buenos]
    texts_limpios = [texts[i] for i in indices_buenos]
    print(f"Goldset depurado. De {len(X)} vectores a {len(X_limpio)}.")

    out_npz = out_dir / "goldset_v2_audited.npz"
    meta_out = dict(meta)
    meta_out.update({
        "audit": {
            "method": "UMAP(2D, cosine) + HDBSCAN(euclidean)",
            "neighbors": int(args.neighbors),
            "min_dist": float(args.min_dist),
            "random_state": int(args.random_state),
            "min_cluster_size": int(args.min_cluster_size),
            "min_samples": int(args.min_samples),
            "noise_pct": float(noise_pct),
            "main_cluster_id": int(main_cluster_id),
        },
        "collection": "goldset_v2_audited",
    })
    np.savez_compressed(
        out_npz,
        texts=np.array(texts_limpios, dtype=object),
        embeddings=np.array(X_limpio, dtype=float),
        ids=np.array(ids_limpios, dtype=object),
        meta=json.dumps(meta_out, ensure_ascii=False),
    )
    print(f"NPZ auditado guardado: {out_npz}")

    if args.upload_uri:
        _upload_to_gcs(out_npz, args.upload_uri)
        print("Recuerda actualizar GOLDSET_NPZ_GCS_URI con la nueva URI.")

if __name__ == "__main__":
    main()

