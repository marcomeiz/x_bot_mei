# 02 · Flujos de Datos

## Camino Feliz
1) Radar (automático): señales de HF/Reddit → candidatos → validación humana en Notion → promoción a `topics_collection`.
2) Ingesta tradicional: PDF → texto → chunks → extracción temas → validación COO → embeddings → `topics_collection`.
3) Generación: `core_generator` elige tema, genera A/B/C ≤280 (iterativo por LLM si requiere), evalúa y propone.
4) Bot: `/g` propone A/B/C con tema+PDF+conteo; callbacks para aprobar y persistir.

## Watchers
- watcher_v2.py / run_watcher.py: proceso asíncrono, no bloquea; tamaños de chunk y solape razonables.
- Salidas: JSON `json/<pdf>.json` y entradas en Chroma.

## Telegram
- Propuesta incluye: Tema (abstract), Origen (PDF), opciones con `(N/280)`.
- Callbacks: `approve_A_<id>`, `approve_B_<id>`, `approve_C_<id>`, `reject_<id>`, `generate_new`.
- Al aprobar: deduplicación en memoria (umbral de similitud) con confirmación si es muy parecido.

