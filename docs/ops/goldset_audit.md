# Auditoría forense del Goldset (UMAP + HDBSCAN)

Fecha: 2025-11-05
Autor: Marco Mei
Propósito: Descontaminar el goldset identificando el cluster principal de “la voz” y aislando el ruido.
Justificación: Los embeddings del goldset incluyen textos analíticos/blandos ajenos a la voz; el LLM queda anclado a ejemplos incorrectos.

## Herramientas
- numpy
- umap-learn (UMAP)
- hdbscan (HDBSCAN)
- matplotlib / seaborn / plotly

Instalación: ver requirements.dev.txt / requirements.runtime.txt.

## Procedimiento
1. Cargar NPZ existente (p.ej. `data/gold_posts/goldset_norm_v1.npz`).
2. Reducir a 2D con UMAP (métrica cosine, n_neighbors=10, min_dist=0.1, random_state=42).
3. Clusterizar con HDBSCAN (euclidean, min_cluster_size=5, min_samples=1, método eom).
4. Visualizar y generar reporte (png + json).
5. Purga: seleccionar el cluster principal (mayor cardinalidad excluyendo ruido) y generar `goldset_v2_audited.npz` con textos/embeddings/ids filtrados.
6. Subir a GCS y actualizar `GOLDSET_NPZ_GCS_URI`.

## Uso
```
python scripts/audit_goldset_umap_hdbscan.py --npz data/gold_posts/goldset_norm_v1.npz --out-dir data/gold_posts \
  --neighbors 10 --min-cluster-size 5 --min-samples 1 --random-state 42 \
  --upload-uri gs://<bucket>/<path>/goldset_v2_audited.npz
```

## Salidas
- `goldset_audit.png`: scatter 2D coloreado por etiqueta de HDBSCAN.
- `goldset_audit.json`: resumen (clusters, tamaños, ruido%).
- `goldset_v2_audited.npz`: NPZ filtrado (keys: texts, embeddings, ids, meta).

## Notas de implementación
- El NPZ soporta `texts/documents`, `embeddings/vectors`, `ids` y `meta`.
- La clave `meta` del NPZ auditado incluye parámetros y resultados de la auditoría.
- Si no se detecta ningún cluster válido, se evita generar el NPZ auditado.
