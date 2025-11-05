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
- Goldset: carga diferida del NPZ (`GOLDSET_NPZ_GCS_URI` prioritario) al primer uso, con logs `GOLDSET_NPZ_LOADED/FAILED` + `DIAG_GOLDSET_READY` y sin fallback legacy.
  - Propósito: garantizar similitud estable y trazable incluso tras reinicios.
  - Justificación: evitar cargas silenciosas y asegurar que cada `variant_evaluation` tenga contexto completo.
  - Autor: AI assistant (codex-cli).
  - Fecha: 2025-11-05.
- Tooling NPZ: nuevo `scripts/validate_goldset_npz.py` + cloudbuild actualizado (`build_goldset_npz.py` + validación) y alias legacy `gen_goldset_npz.py`.
  - Propósito: formalizar el contrato del goldset, detectar NPZ inválidos y evitar scripts divergentes.
  - Justificación: el pipeline usaba un generador antiguo sin textos/meta ni validador; ahora se estandariza antes de subir/promover.
  - Autor: AI assistant (codex-cli).
  - Fecha: 2025-11-05.
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
## 2025-11-05 (Acid Test)
- Desacoplar Style-RAG del tema con recuperación aleatoria y semilla fija.
  - Propósito: comprobar si la búsqueda semántica está envenenando el estilo.
  - Cambios:
    - core_generator: se elimina el prefetch semántico de gold_examples y se delega en variant_generators la recuperación aleatoria (k=5, semilla 1337).
    - variant_generators: usa retrieve_goldset_examples_random_meta(k=5) con logging estructurado de style_rag_examples (id/idx/text/scope/collection).
    - src/goldset.py: añade GOLDSET_RANDOM_SEED=1337 y fallback cuando falta NPZ (carga textos puros para permitir RANDOM sin embeddings).
  - Métricas/diagnósticos esperados: g_goldset_random_retrieve, generation_prompt_gold_block con anchors_count=5 y evento style_rag_examples.
  - Justificación: si el goldset es consistente en estilo, muestras aleatorias deberían mantener/mejorar el tono; si no, revela inconsistencia del goldset.
## Revert: Restore semantic Style-RAG (NN)

- Reverted the Acid Test changes that forced random goldset retrieval with k=5.
- Restored semantic Style-RAG using `retrieve_goldset_examples_nn` with k=3 for both variant and comment generation.
- Removed usage of `retrieve_goldset_examples_random_meta` and its associated `g_goldset_random_retrieve` diagnostics.
- Kept `anchors_count` visibility log but without an "expected=5" assertion.
- This confirms the test’s conclusion: the goldset quality/coverage is the real issue; generation is back to topic-aware NN anchoring.
## 2025-11-05 - Auditoría Goldset (UMAP + HDBSCAN)
Autor: Marco Mei
Propósito: Descontaminar el goldset identificando y reteniendo únicamente el cluster principal de "la voz".
Justificación: El goldset contenía embeddings con estilo analítico/blando (LinkedIn-like), contaminando el anclaje de Style-RAG.

- Se agregó `scripts/audit_goldset_umap_hdbscan.py` para:
  - Cargar NPZ (texts/embeddings/ids/meta), reducir con UMAP (cosine) y clusterizar con HDBSCAN (euclidean).
  - Generar `goldset_audit.png` y `goldset_audit.json` con conteos y % de ruido.
  - Producir `goldset_v2_audited.npz` con miembros del cluster principal.
  - Opción `--upload-uri` para subir a GCS y actualizar `GOLDSET_NPZ_GCS_URI`.
- Dependencias añadidas a `requirements.dev.txt` y `requirements.runtime.txt`: umap-learn, hdbscan, matplotlib, seaborn, plotly.
- Documentación: `docs/ops/goldset_audit.md` con procedimiento y uso.
- Limpieza: Eliminadas utilidades del Acid Test (random_meta/random) y sus dependencias del runtime.
