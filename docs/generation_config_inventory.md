# Generation Config Inventory

This note tracks every configuration surface that impacts tweet/comment generation so we can verify nothing queda hardcodeado. Use it as a checklist before touching code.

## 1. Prompt Sources
- `prompts/generation/all_variants.md` → tweet variants (externalizado ✅).
- `prompts/comments/generation_v5_1.md` → protocolo de comentarios (externalizado ✅).
- `prompts/comments/audit.md` → auditor de comentarios (externo ✅).
- `prompts/generation/tail_sampling.md` y `prompts/generation/contrast_analysis.md` → tail sampling & contrast (externalizado ✅).

## 2. Guardrails & Thresholds
- `config/warden.yaml` define toggles y rangos (commas, words/line, mid/long chars, minimal mode, em dash). ENV (`WARDEN_CONFIG_PATH`, `ENFORCE_NO_COMMAS`, etc.) siguen como overrides.
- Banned words/suffixes/stopwords codificados en `writing_rules.py` y `variant_generators.py`. Objetivo: `config/lexicon.json`.
- Estilo (`StyleRejection` thresholds) disperso en `style_guard.py` → migrar a `config/style_audit.yaml`.

## 3. Runtime Messages
- Telegram fallbacks/mensajes de error en `proposal_service.py` y `telegram_client.py`.
- Logs de guardian (`style_guard`, `proposal_service`) tienen textos fijos → mover a `config/messages.yaml`.

## 4. Environment Switches
- Variables `.env` críticas documentadas en `README.md` y `AGENTS/05_STACK_CONFIG.md`.
- Validar en runtime que cada constante se origina del archivo/config esperado (Stage 0.1 – logging de verificación).

## Próximos pasos
1. Externalizar léxicos (`BANNED_WORDS`, `BANNED_SUFFIXES`, `STOPWORDS`) a `config/lexicon.json`.
2. Llevar umbrales de `style_guard`/auditoría a `config/style_audit.yaml`.
3. Consolidar mensajes de usuario/logs en `config/messages.yaml`.
4. Añadir logging de verificación (Stage 0.1) y pruebas de carga dinámica.

Actualizar este documento tras cada etapa para mantener trazabilidad.
