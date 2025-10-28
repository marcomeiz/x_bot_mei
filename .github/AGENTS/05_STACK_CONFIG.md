# 05 · Stack y Configuración

## Variables de entorno
- GOOGLE_API_KEY (obligatoria) — embeddings y Gemini.
- OPENROUTER_API_KEY (opcional) — fallback OpenRouter.
- FALLBACK_PROVIDER_ORDER — por defecto `gemini,openrouter`.
- GEMINI_MODEL — por defecto `gemini-2.5-pro`.
- OPENROUTER_DEFAULT_MODEL — sugerido `anthropic/claude-3.5-sonnet`.
- CHROMA_DB_PATH — ruta persistencia (Cloud Run: `/mnt/db`).
- SHOW_TOPIC_ID — mostrar ID en Telegram (0/1).

## Servicios
- ChromaDB — colecciones: `topics_collection` y `memory_collection` (cosine).
- Telegram — parse HTML con fallback a texto plano.
- Notion / HF — sincronización opcional de candidatos.

## Límites y rutas
- GCS FUSE para `/mnt/db` en Cloud Run.
- Watcher escribe JSON en `json/` y mueve PDFs a `processed_pdfs/`.

