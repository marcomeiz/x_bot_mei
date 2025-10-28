# 06 · Runbook y Pruebas

## Pruebas (rápido y seguro)
- Smoke sin red: monkeypatch del LLM para forzar fallback y validar JSON y ≤280.
- Evaluación: cargar `config/evaluation_*.yaml` y validar formato.
- Watcher local: `python run_watcher.py` → copiar PDF a `uploads/` → comprobar `json/` y `db/`.

## Diagnóstico
- Logs Cloud Run:
  - Filtros por `/g`, “Tema seleccionado”, “Propuesta enviada”, “Telegram API error”.
  - Gemini 4xx → fallback a OpenRouter; OpenRouter 4xx → revisar `OPENROUTER_DEFAULT_MODEL`.
- Telegram 400: fallback a texto plano.
- Chroma errors: revisar permisos y path `CHROMA_DB_PATH`.

## Operación
- Fresh start DB: borrar `db/` (o bucket) y reiniciar watchers/bot.
- Rotación de claves: actualizar Secret Manager y redeploy.
- Priorizar proveedor: ajustar `FALLBACK_PROVIDER_ORDER` y redeploy.

