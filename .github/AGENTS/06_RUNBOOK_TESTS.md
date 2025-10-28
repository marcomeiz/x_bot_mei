# 06 · Runbook y Pruebas

## Pruebas (rápido y seguro)
- Smoke sin red: monkeypatch del LLM para forzar fallback y validar JSON y ≤280.
- Evaluación: cargar `config/evaluation_*.yaml` y validar formato.
- Watcher local: `python run_watcher.py` → copiar PDF a `uploads/` → comprobar `json/` y `db/`.

## Diagnóstico
- Logs Cloud Run:
  - Filtros por `/g`, “Tema seleccionado”, “Propuesta enviada”, “Telegram API error”.
  - OpenRouter embeddings: buscar `Embeddings HTTP error`, `Embedding HTTP response missing data array`, `Embedding circuit open`.
  - Evaluación: no deben aparecer modelos `gemini-*`. Usar `EVAL_FAST_MODEL`/`EVAL_SLOW_MODEL` si es necesario.
- Telegram 400: fallback a texto plano.
- Chroma errors: revisar permisos y path `CHROMA_DB_PATH`.

## Operación
- Fresh start DB: borrar `db/` (o bucket) y reiniciar watchers/bot.
- Rotación de claves: actualizar Secret Manager y redeploy.
- Proveedor: OpenRouter-only con `OPENROUTER_API_KEY`.
- Ajustar modelos baratos por entorno: `POST_MODEL`, `EVAL_FAST_MODEL`, `EVAL_SLOW_MODEL`, `COMMENT_*_MODEL`, `TOPIC_EXTRACTION_MODEL`, `EMBED_MODEL`.
