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
- Logs de guardian (`style_guard`, `proposal_service`) tienen textos fijos pendientes de migrar si aplica.

## 4. Environment Switches
- Variables `.env` críticas documentadas en `README.md` y `AGENTS/05_STACK_CONFIG.md`.
- Validar en runtime que cada constante se origina del archivo/config esperado (Stage 0.1 – logging de verificación).

## Próximos pasos
1. Consolidar mensajes de usuario/logs en `config/messages.yaml`.
2. Añadir logging de verificación (Stage 0.1) y pruebas de carga dinámica.

Actualizar este documento tras cada etapa para mantener trazabilidad.
