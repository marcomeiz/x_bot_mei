# 05 · Stack y Configuración

## Variables de entorno
- GOOGLE_API_KEY (obligatoria) — embeddings y Gemini.
- OPENROUTER_API_KEY (opcional) — fallback OpenRouter.
- Proveedor único (actual): OpenRouter-only (`OPENROUTER_API_KEY`).
- Modelos por propósito:
  - `POST_MODEL` (default: `qwen/qwen-2.5-7b-instruct`)
  - `EVAL_FAST_MODEL` (default: `qwen/qwen-2.5-7b-instruct`)
  - `EVAL_SLOW_MODEL` (default: `mistralai/mistral-nemo`)
  - `COMMENT_AUDIT_MODEL` (default: `qwen/qwen-2.5-7b-instruct`)
  - `COMMENT_REWRITE_MODEL` (default: `mistralai/mistral-nemo`)
  - `TOPIC_EXTRACTION_MODEL` (default: `mistralai/mistral-nemo`)
  - `EMBED_MODEL` (default: `openai/text-embedding-3-small`)
- CHROMA_DB_PATH — ruta persistencia (Cloud Run: `/mnt/db`).
- SHOW_TOPIC_ID — mostrar ID en Telegram (0/1).

## Servicios
- ChromaDB — colecciones: `topics_collection` y `memory_collection` (cosine).
- Telegram — parse HTML con fallback a texto plano.
- Notion / HF — sincronización opcional de candidatos.

## Límites y rutas
- GCS FUSE para `/mnt/db` en Cloud Run.
- Watcher escribe JSON en `json/` y mueve PDFs a `processed_pdfs/`.
