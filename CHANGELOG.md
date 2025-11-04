# Changelog

## [Unreleased]
- Métricas: nuevo módulo metrics.py con KPIs y temporizadores
- Instrumentación en embeddings_manager.py para cache lookup/generación
- Pruebas unitarias de caché y fingerprint
- CI (GitHub Actions) con pytest
- Docs: ARCHITECTURE_GCP.md, MAINTENANCE_SCALING.md, TECH_COMPARISON.md
- Backfill Firestore desde Chroma (scripts/backfill_firestore_from_chroma.py)

## 2025-11-04
- Eliminada guía temporal `docs/TASK_POPULATE_TOPICS.md` tras verificar:
  - Población de tópicos con embeddings precomputados (topics_dim=3072).
  - Política cache-only efectiva en `/g` (solo métricas `emb_skip_due_to_policy`, sin "Generando embedding" post-trigger).
- No se detectaron duplicaciones de funciones en el runtime de embeddings.
 - Fix: robustez en carga de caché de embeddings desde ChromaDB para evitar `The truth value of an array is ambiguous`.
   - `embeddings_manager._chroma_load`: reemplazo de `or []` por conversión segura a lista; flatten defensivo.
   - `src/goldset._load_embeddings_from_chroma`: conversión segura a listas y validación por tamaño.
 - Alineación con documentación: orden de modelos fallback actualizado a `openai/text-embedding-3-small → thenlper/gte-small → jinaai/jina-embeddings-v2-base-en`.

- Nuevo: modo de variantes adaptativo (`VARIANT_MODE=adaptive`).
  - Propósito: reducir coste/latencia generando una sola variante creativa (mid) y derivando short/long por reescritura.
  - Justificación: early-stop cuando la similitud al goldset supera `GOLDSET_MIN_SIMILARITY + ADAPTIVE_HOLGURA` y cumple segunda persona.
  - Cambios:
    - `core_generator.generate_tweet_from_topic`: rama adaptativa con pipeline mid → compress_to_short → expand_to_long y early-stop.
    - `variant_generators.py`: helpers `compress_to_short()` y `expand_to_long()` basados en `ensure_char_range_via_llm`.
    - Documentación: `docs/generation_config_inventory.md` actualizado con nuevas variables y criterios.
  - Autor: AI assistant.
  - Fecha: 2025-11-04.

- Congelación de esquema de logs y nuevos campos en `diagnostics_logger.log_post_metrics`.
  - Propósito: mantener compatibilidad retroactiva y añadir trazabilidad del pipeline y del origen de cada variante.
  - Cambios:
    - Añadido `pipeline_version` (desde ENV `PIPELINE_VERSION`, default `legacy_v1`).
    - Añadido `variant_source` (extra: `gen | refine | derive`, default `gen`).
    - `proposal_service`: se pasa `variant_source=refine` en logs de evaluación cuando aplica.
    - Documentación: `docs/generation_config_inventory.md` actualizado con nueva ENV y guía de uso.
  - Autor: AI assistant.
  - Fecha: 2025-11-04.
