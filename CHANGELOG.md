# Changelog

## [Unreleased]
- Métricas: nuevo módulo metrics.py con KPIs y temporizadores
- Instrumentación en embeddings_manager.py para cache lookup/generación
- Pruebas unitarias de caché y fingerprint
- CI (GitHub Actions) con pytest

### 2025-11-06 — Restauración Crítica del Sistema de Comentarios v5.1
Autor: AI Assistant (Claude Code)

Propósito: Resolver merge conflict crítico y restaurar sistema completo de generación de comentarios que quedó truncado en refactor previo.
Justificación: El sistema de comentarios (funcionalidad activa en producción) quedó roto tras un refactor incompleto que eliminó ~89% del código de variant_generators.py (1,956 líneas) y dejó un merge conflict sin resolver que impedía la ejecución del bot.

**Problema Principal Identificado:**
- Merge conflict sin resolver en core_generator.py (líneas 27-34) causando SyntaxError
- Archivo variant_generators.py truncado (235 líneas vs 2,191 originales - 89% faltante)
- Funciones críticas eliminadas pero aún importadas/usadas: generate_comment_from_text, assess_comment_opportunity, generate_comment_reply
- Sistema de comentarios activo en bot.py pero con dependencias rotas

**Cambios Realizados:**

1. **core_generator.py**:
   - Resuelto merge conflict en imports (líneas 24-31)
   - Restaurada función generate_comment_from_text() desde commit 7c49857 (líneas 295-343)
   - Actualizados imports para incluir CommentResult y CommentAssessment desde variant_generators
   - Eliminado comentario obsoleto que indicaba incorrectamente que el sistema estaba "fully deprecated"

2. **variant_generators.py**:
   - Restauración completa del archivo desde commit 7c49857 (235 → 2,191 líneas)
   - Funciones restauradas: generate_comment_reply(), assess_comment_opportunity(), y 30+ funciones helper
   - Sistema completo de comentarios v5.1 con "Insight Engine Protocol" operativo
   - Integración validada con prompt prompts/comments/generation_v5_1.md (6 pasos: Deconstruir → Filtrar → Principios → Conexiones → Sintetizar → Inyección de Imperfección)

3. **bot.py**:
   - Reemplazado debug print inapropiado (línea 2)
   - Antes: "---- ESTA ES LA PUTA VERSIÓN NUEVA DEL CÓDIGO: v_FINAL ----"
   - Después: "[SYSTEM] X Bot Mei v2.0 - Production Build Initialized"

**Validación:**
- Código restaurado implementa correctamente estrategia v5.1 documentada en COMMENT_VOICE_V5_STRATEGY.md
- Prompt generation_v5_1.md incluye los 6 pasos del protocolo "Insight Engine"
- Imports validados: todas las funciones críticas ahora resuelven correctamente
- Sistema de comentarios completamente funcional para producción

**Impacto:**
- Sistema bloqueado → Sistema operacional
- 0 funciones de comentarios → 30+ funciones restauradas
- SyntaxError crítico eliminado
- Funcionalidad de comentarios en bot.py restaurada completamente

Fecha: 2025-11-06

---

### 2025-11-05 — Reemplazo de Juez binario por Juez‑Calificador (Grader)
Autor: TraeAI

Propósito: Eliminar el juez binario (true/false) y capturar razonamiento y puntuaciones por pilar para diagnósticos.
Justificación: El juez binario no explica por qué falla un borrador; se requiere trazabilidad.

Cambios:
- prompts/validation/style_judge_v1.md: Prompt reemplazado por versión JSON (cumple_contrato, razonamiento_principal, puntuacion_tono/diccion/ritmo).
- proposal_service._check_style_with_llm: Parseo de JSON del Grader, logging de razonamiento y puntuaciones via diagnostics_logger.log_post_metrics; devuelve solo el booleano.
- proposal_service._check_contract_requirements: Pasa contexto (piece_id, label, event_stage, variant_source) al Grader.
- diagnostics_logger.log_post_metrics: Se agregan campos judge_reasoning, judge_tono, judge_diccion, judge_ritmo en el payload de variant_evaluation.
- Documentación actualizada (este CHANGELOG, docs/GENERATION_WARDEN.md, docs/generation_config_inventory.md).

Impacto: Se mantienen compatibilidades; los nuevos campos son opcionales en logs y no rompen consultas existentes. Tests: 16 passed, 6 warnings.
- Docs: ARCHITECTURE_GCP.md, MAINTENANCE_SCALING.md, TECH_COMPARISON.md
- Backfill Firestore desde Chroma (scripts/backfill_firestore_from_chroma.py)

### Cambio: Eliminación del linter y hard checks del Warden en post‑process; validación pasa al Juez LLM
- Propósito: Evitar doble enforcement y choques entre reglas heurísticas (commas/and‑or/char ranges) y el contrato de estilo. La decisión de envío ahora depende de un juez LLM binario.
- Justificación: Los rechazos por linter en `variant_generators.py` generaban conflictos con evaluaciones posteriores; el juez LLM en `proposal_service.py` es más fiable al aplicar `<STYLE_CONTRACT>` con true/false.
- Cambios:
  - `variant_generators._validate_variant`: solo limpieza básica (quitar hashtags + colapsar espacios); elimina `improve_style`, reparaciones mecánicas y hard checks.
  - Se removieron imports de linter (`improve_style`, `label_sections`, `revise_for_style`) del post‑process.
  - Documentación actualizada: `docs/GENERATION_WARDEN.md` refleja Porter como limpieza básica y valida por LLM Judge; `docs/generation_config_inventory.md` actualizado.
- Autor: Mei
- Fecha: 2025-11-05

### Cambio: Sustitución de validación por similitud (cosine) por Juez LLM (proposal_service)
- Propósito: Validar estrictamente el estilo de cada borrador contra el STYLE_CONTRACT usando un LLM que responde solo true/false, eliminando ambigüedad por métricas de similitud.
- Justificación: La verificación basada en similitud al goldset no garantiza cumplimiento estricto del contrato; un juez LLM reduce falsos positivos y simplifica el control de flujo con `all([bools])`.
- Cambios:
  - Nuevo prompt `prompts/validation/style_judge_v1.md` con System/User y placeholders `{style_contract_text}` y `{draft_text}`.
  - `proposal_service._check_contract_requirements`: elimina toda lógica de embeddings/similitud; ahora itera sobre short/mid/long y llama a `_check_style_with_llm()` devolviendo `[True|False,...]`.
  - `proposal_service._check_style_with_llm`: carga el contrato (11 puntos), renderiza el prompt y llama al LLM rápido; limpia la respuesta y retorna bool.
  - `proposal_service.propose_tweet`: utiliza `all(check_results_pre/post)` para decidir envío/rehint y actualiza logs de control.
- Autor: Mei
- Fecha: 2025-11-05

### Fix: Bucle de reintentos trataba éxito como fallo (proposal_service)
- Propósito: Evitar reintentos innecesarios cuando todas las variantes generadas pasan los checks de contrato/similitud del goldset.
- Justificación: Los logs mostraban `_check_contract_and_goldset` con `passed=true` para las 3 variantes (sim ≥ 0.77) y, aun así, el bot enviaba "⚠️ Variantes no cumplen el contrato. Reintentando…".
- Cambios:
  - `proposal_service.propose_tweet`: se calcula `all_passed_pre` y `all_passed_post` a partir del mapa de similitudes devuelto por `_check_contract_requirements` y se corta la ruta de reintento cuando `all(...)` es verdadero.
  - Se añaden logs de control: "[CONTROL] Todos los drafts pasaron (...). Enviando al usuario." para trazabilidad.
- Autor: Mei
- Fecha: 2025-11-05

## 2025-11-05
- Introducción del prompt limpio `prompts/generation/all_variants_v4.md` y cambio de referencia en `variant_generators.generate_all_variants`.
  - Propósito: eliminar conflicto entre el User Prompt y el `<STYLE_CONTRACT>` del System Prompt; suprimir reglas rígidas de "Voice & Audience" que dañan la variación humana.
  - Justificación: la señal de estilo proveniente de los 5 anchors es robusta pero frágil frente a instrucciones contradictorias; el nuevo prompt limpia la arquitectura y centraliza la inyección de anchors vía `{gold_examples_block}`.
  - Autor: AI assistant.
  - Fecha: 2025-11-05.
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

## 2025-11-05 - Style-RAG anchors: RANDOM k=5 (Active)
Autor: Marco Mei
Propósito: Reforzar la señal de estilo tomando 5 ejemplos aleatorios del goldset auditado (21 vectores) en generación y comentarios.
Justificación: Con el goldset v2 depurado, la aleatoriedad reduce sesgo por tópico y mantiene voz doctrinal más fuerte.

- variant_generators: cambia a `retrieve_goldset_examples_random(k=5)` con telemetría `g_goldset_random_retrieve` y logs de `anchors_count`.
- src/goldset.py: reintroduce `retrieve_goldset_examples_random(k)` sobre el NPZ activo.
- Deploy: `GOLDSET_NPZ_GCS_URI` se mantiene apuntando a `gs://xbot-473616-x-bot-mei-db/goldset/goldset_v2_audited.npz` en Cloud Run (cloudbuild y script manual actualizados).
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
