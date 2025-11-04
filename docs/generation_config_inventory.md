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

Actualizar este documento tras cada etapa para mantener trazabilidad.
