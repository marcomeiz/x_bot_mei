# Generation Config Inventory

This note tracks every configuration surface that impacts tweet/comment generation so we can verify nothing queda hardcodeado. Use it as a checklist before touching code.

## 1. Prompt Sources
- `prompts/generation/all_variants.md` → tweet variants (externalizado ✅).
- `prompts/comments/generation_v5_1.md` → protocolo de comentarios (externalizado ✅).
- `prompts/comments/audit.md` → auditor de comentarios (externo ✅).
- `prompts/generation/tail_sampling.md` y `prompts/generation/contrast_analysis.md` → tail sampling & contrast (externalizado ✅).

## 2. Guardrails & Thresholds
- Caracteres objetivo (`MID_MIN`, `LONG_MAX`, `WARDEN_*`, `COMMENT` ≤140) definidos via `os.getenv` en `variant_generators.py`. Requiere `config/warden.yaml`. 
- Banned words/suffixes/stopwords codificados en `writing_rules.py` y `variant_generators.py`. Objetivo: `config/lexicon.json`.
- Estilo (`StyleRejection` thresholds) disperso en `style_guard.py` → migrar a `config/style_audit.yaml`.

## 3. Runtime Messages
- Telegram fallbacks/mensajes de error en `proposal_service.py` y `telegram_client.py`.
- Logs de guardian (`style_guard`, `proposal_service`) tienen textos fijos → mover a `config/messages.yaml`.

## 4. Environment Switches
- Variables `.env` críticas documentadas en `README.md` y `AGENTS/05_STACK_CONFIG.md`.
- Validar en runtime que cada constante se origina del archivo/config esperado (Stage 0.1 – logging de verificación).

## Próximos pasos
1. Diseñar `config/warden.yaml` y `config/lexicon.json`, ajustar `AppSettings`.
2. Externalizar thresholds/umbrales de estilo (`style_guard`) a `config/style_audit.yaml`.
3. Consolidar mensajes de usuario/logs en `config/messages.yaml`.
4. Añadir logging de verificación (Stage 0.1) y pruebas de carga dinámica.

Actualizar este documento tras cada etapa para mantener trazabilidad.
