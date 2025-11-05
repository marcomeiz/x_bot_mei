# Changelog

## [Unreleased]
- Métricas: nuevo módulo metrics.py con KPIs y temporizadores
- Instrumentación en embeddings_manager.py para cache lookup/generación
- Pruebas unitarias de caché y fingerprint
- CI (GitHub Actions) con pytest
- Docs: ARCHITECTURE_GCP.md, MAINTENANCE_SCALING.md, TECH_COMPARISON.md
- Backfill Firestore desde Chroma (scripts/backfill_firestore_from_chroma.py)

## 2025-11-05
- Consolidación de utilitarios Chroma en `src/chroma_utils.py` y adopción en `admin_service.py`, `persistence_service.py`, `core_generator.py` y `scripts/migrate_local_chroma_to_remote.py`.
  - Propósito: eliminar funciones duplicadas para aplanar respuestas de Chroma y centralizar la detección de IDs existentes.
  - Justificación: evitar incoherencias entre servicios, mejorar mantenibilidad y reducir bugs sutiles con listas anidadas.
  - Autor: AI assistant (codex-cli).
  - Fecha: 2025-11-05.
- Limpieza de scripts de goldset (`scripts/build_goldset_npz.py`): obligatorio importar `normalize_for_embedding` desde `src.normalization` eliminando el fallback local.
  - Propósito: prevenir drift de normalización y mantener una única ruta de normalizado.
  - Justificación: el fallback reintroducía lógica divergente y permitía ejecuciones fuera del estándar del repo.
  - Autor: AI assistant (codex-cli).
  - Fecha: 2025-11-05.
- Eliminación de dataclasses sin uso (`ABGenerationResult`, `VariantCResult`) en `variant_generators.py`.
  - Propósito: reducir código muerto y aclarar la API real expuesta por el generador.
  - Justificación: ninguna referencia activa; su presencia inducía a error durante la revisión de duplicados.
  - Autor: AI assistant (codex-cli).
  - Fecha: 2025-11-05.
- Reactivación del log de `variant_evaluation` en `proposal_service._check_contract_requirements`, etiquetando `event_stage` (`pre`/`final`) para garantizar la actualización con similitud real.
  - Propósito: asegurar que cada variante evaluada emita el evento estructurado (similarity/min_required/passed) y que la segunda pasada entregue el valor definitivo cuando el embedding ya está disponible.
  - Justificación: tras el refactor se saltaba el hook cuando la similitud venía `None`, dejando la consola sin métricas; ahora el evento se emite siempre y la actualización final queda diferenciada.
- Goldset: carga NPZ en import-time (`GOLDSET_NPZ_GCS_URI` prioritario), logs `GOLDSET_NPZ_LOADED/FAILED` y `DIAG_GOLDSET_READY`, sin fallback legacy.
  - Propósito: asegurar que runtime usa el NPZ versionado del bucket y expone identificadores estables para telemetría.
  - Justificación: necesitábamos trazabilidad del match en logs y evitar que `goldset_collection` aparezca como `unknown`.
- Logs estructurados: `diagnostics_logger.log_post_metrics` conserva siempre `similarity`, añade `similarity_raw`, `similarity_norm`, `max_pair_id` y etiqueta `event_stage`.
  - Propósito: permitir filtros consistentes en Cloud Logging y diferenciar entre medición preliminar y definitiva.
  - Justificación: las consultas fallaban cuando el campo se omitía; ahora siempre aparece (null o valor real) junto con metadatos extendidos.
- Etapas de evaluación: `_check_contract_requirements` reporta `event_stage` como `PRE`/`POST` para alinear con las consultas operativas.
  - Propósito: facilitar dashboards que distinguen entre chequeo inicial y evaluación final.
  - Justificación: los analistas rastrean explícitamente `POST`; el valor `final` no coincidía con los filtros desplegados.
  - Autor: AI assistant (codex-cli).
  - Fecha: 2025-11-05.

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
