# Generation Config Inventory

This note tracks every configuration surface that impacts tweet/comment generation so we can verify nothing queda hardcodeado. Use it as a checklist before touching code.

## 1. Prompt Sources
- `prompts/generation/all_variants.md` → tweet variants (externalizado ✅).
- `prompts/comments/generation_v5_1.md` → protocolo de comentarios (externalizado ✅).
- `prompts/comments/audit.md` → auditor de comentarios (externo ✅).
- `prompts/generation/tail_sampling.md` y `prompts/generation/contrast_analysis.md` → tail sampling & contrast (externalizado ✅).

## 2. Guardrails & Thresholds
- `config/warden.yaml` define toggles y rangos (commas, words/line, mid/long chars, minimal mode, em dash). ENV (`WARDEN_CONFIG_PATH`, `ENFORCE_NO_COMMAS`, etc.) siguen como overrides.
- `config/lexicon.json` provee listas de palabras vetadas/sufijos/stopwords (consumido vía `src/lexicon.py` por `writing_rules` y `variant_generators`).
- `config/style_audit.yaml` controla enforcement, rondas y umbrales del guardián de estilo (cargado vía `src/style_config.py` con overrides ENV).
- `src/goldset.py` + `GOLDSET_MIN_SIMILARITY` verifican que cada borrador mantenga similitud mínima con el gold set.

## 3. Runtime Messages
- `config/messages.yaml` centraliza mensajes de usuario para bot/propuesta/avisos (cargado vía `src/messages.py`).
- Telegram fallbacks/mensajes de error en `proposal_service.py` y `telegram_client.py`.
- `LOG_GENERATED_VARIANTS` (env) activa el log detallado de cada variante + motivo de rechazo; útil para post-mortem cuando el contrato frena propuestas.
- Logs de guardian (`style_guard`, `proposal_service`) tienen textos fijos pendientes de migrar si aplica.

## 4. Environment Switches
- Variables `.env` críticas documentadas en `README.md` y `AGENTS/05_STACK_CONFIG.md`.
- Validar en runtime que cada constante se origina del archivo/config esperado (Stage 0.1 – logging de verificación).

### Nuevo: Versión de pipeline (congelación de esquema de logs)
- `PIPELINE_VERSION` controla el valor incluido en los logs estructurados para trazabilidad de la versión de la tubería. Por defecto: `legacy_v1`. Ejemplos: `adaptive_v1`, `legacy_v1`.

### Nuevo: Modo de variantes adaptativo
- `VARIANT_MODE=adaptive` activa una generación secuencial con early-stop.
- `ADAPTIVE_MAX_VARIANTS=3` límite superior de variantes producidas (por defecto 3: mid → short → long).
- `ADAPTIVE_HOLGURA=0.03` margen adicional sobre `GOLDSET_MIN_SIMILARITY` para considerar un "strong pass" y detener la tubería.

Pipeline adaptativo:
- Paso 1: generate_mid() – una sola generación creativa por defecto.
- Paso 2: compress_to_short() – reescritura hacia ≤160 chars si no hubo early-stop.
- Paso 3: expand_to_long() – reescritura hacia 240–280 chars si no hubo early-stop.

Criterio de early-stop:
- Se detiene si la variante generada habla en segunda persona (you/you're/you'll/your) y su similitud con el goldset ≥ `GOLDSET_MIN_SIMILARITY + ADAPTIVE_HOLGURA`.

## Próximos pasos
1. Consolidar mensajes de usuario/logs en `config/messages.yaml`.
2. Añadir logging de verificación (Stage 0.1) y pruebas de carga dinámica.

---

**Cambio:** Ajuste del prompt `prompts/generation/all_variants.md` para reforzar la ancla belief/ownership con tensión/paradoja explícita.  
**Fecha:** 2025-11-04  
**Autor:** AI assistant  
**Justificación:** Incrementar similitud media y alineación con el eje belief del autor sin relajar guardrails ni umbrales.

**Cambio:** Registro de métrica `has_anchor` en `diagnostics_logger.compute_basic_metrics` para auditar anclas concretas en logs estructurados.  
**Fecha:** 2025-11-04  
**Autor:** AI assistant  
**Justificación:** Sustituir flags rígidos previos por una verificación de anclas (número/hecho/ejemplo) y alimentar telemetría sin depender de `missing_number` ni reglas similares.

**Cambio:** Emisión de un log estructurado por variante en `diagnostics_logger.log_post_metrics` para Cloud Logging (`jsonPayload`).  
**Fecha:** 2025-11-04  
**Autor:** AI assistant  
**Justificación:** Garantizar trazabilidad pieza/variant con campos `piece_id`, `variant`, `draft_text`, `similarity`, `min_required`, `passed`, `timestamp` para análisis y depuración.

**Cambio:** Normalización unificada (`src/normalization.py`) aplicada antes de generar embeddings en runtime (`embeddings_manager.get_embedding`) y goldset (`src/goldset._compute_embeddings_locally`).  
**Fecha:** 2025-11-04  
**Autor:** AI assistant  
**Justificación:** Alinear pipelines de similitud para usar minúsculas, filtrado de URLs/handles/emojis y normalización ASCII antes de cualquier embedding, evitando divergencias entre ingesta y generación.

**Cambio:** Colección versionada `goldset_norm_v1` (script `scripts/build_goldset_norm_v1.py`) con metadatos emb_model/emb_dim/normalizer_version y log `GOLDSET_READY`.  
**Fecha:** 2025-11-04  
**Autor:** AI assistant  
**Justificación:** Garantizar que runtime solo lea embeddings normalizados, con trazabilidad de versión y recuento desde Cloud Logging.

**Cambio:** `diagnostics_logger` emite `DIAG_STRUCTURED_OK` al inicializar el handler `StructuredLogHandler` (Cloud Logging) con payload `jsonPayload`.
**Fecha:** 2025-11-04  
**Autor:** AI assistant  
**Justificación:** Validar rápidamente que Cloud Run recibe eventos estructurados antes de inspeccionar variantes.

**Cambio:** Introducción de `VARIANT_MODE=adaptive` con pipeline secuencial mid → short → long y early-stop por holgura.  
**Fecha:** 2025-11-04  
**Autor:** AI assistant  
**Justificación:** Reducir costo/latencia generando una sola variante creativa y derivando las otras por reescritura controlada; detener cuando la calidad/estilo supera el umbral con margen.

**Cambio:** Nuevos helpers `compress_to_short()` y `expand_to_long()` en `variant_generators.py`.  
**Fecha:** 2025-11-04  
**Autor:** AI assistant  
**Justificación:** Encapsular reescrituras hacia los rangos objetivo de caracteres reutilizando `ensure_char_range_via_llm` y respetando guardrails.

**Cambio:** Congelación del esquema de logs en `diagnostics_logger.log_post_metrics` y adición de campos `pipeline_version` y `variant_source`.  
**Fecha:** 2025-11-04  
**Autor:** AI assistant  
**Justificación:** Mantener compatibilidad retroactiva con consultas existentes mientras se añade trazabilidad de la versión del pipeline y el origen de cada variante (gen | refine | derive).

Notas de uso:
- `pipeline_version` se fija desde ENV `PIPELINE_VERSION` (default `legacy_v1`).
- `variant_source` se pasa vía `extra` al logger: por defecto `gen`; usar `refine` cuando la variante haya sido reescrita y `derive` cuando provenga de derivación/compresión/expansión.

Actualizar este documento tras cada etapa para mantener trazabilidad.

**Cambio:** Inyección Style-RAG de ejemplos del goldset por similitud semántica (NN) en `variant_generators.generate_all_variants` y `generate_comment_reply`.  
**Fecha:** 2025-11-05  
**Autor:** AI assistant  
**Justificación:** Sustituir selección fija/aleatoria por recuperación Top-K de ejemplos más cercanos al tópico para reforzar la voz con contexto específico y elevar la similitud de embedding.

**Cambio:** Ajuste del prompt `prompts/generation/all_variants.md` para aceptar `{gold_examples_block}` desde el loader y evitar lógica Jinja dentro de la plantilla.  
**Fecha:** 2025-11-05  
**Autor:** AI assistant  
**Justificación:** Simplificar inyección de anchors desde código y prevenir render parcial cuando falten ejemplos, manteniendo control del formato en `variant_generators`.

**Cambio:** Marcar como deprecado `src/goldset.retrieve_goldset_examples` y recomendar `retrieve_goldset_examples_nn`.  
**Fecha:** 2025-11-05  
**Autor:** AI assistant  
**Justificación:** Consolidar la recuperación en un solo método NN para evitar divergencias y asegurar consistencia entre generación y comentarios.
