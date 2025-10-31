# 05 · Stack y Configuración

**Regla dura:** si un valor puede residir en env vars, YAML, JSON o prompts, no se hardcodea en código. Este archivo es la primera parada antes de introducir cualquier constante nueva.

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

## Configuración clave
- `config/warden.yaml` — toggles y rangos del Warden (commas, palabras/linea, mid/long chars). Sobrescribe con `WARDEN_CONFIG_PATH` o env específicos si necesitas ajustes rápidos.
- `config/lexicon.json` — palabras vetadas, sufijos, stopwords. Sobrescribe con `LEXICON_CONFIG_PATH` o env dedicadas.
- `config/style_audit.yaml` — umbrales del guardián de estilo (enforce, rondas, hedging/jargon scores, memoria). Sobrescribe con `STYLE_AUDIT_CONFIG_PATH` o env (`STYLE_*`).
- Gold set: `data/gold_posts/` + `GOLDSET_MIN_SIMILARITY` controlan la proximidad mínima a la voz. Usa `GOLDSET_DATA_PATH`/`GOLDSET_COLLECTION_NAME` si necesitas rutas propias.

## Límites y rutas
- GCS FUSE para `/mnt/db` en Cloud Run.
- Watcher escribe JSON en `json/` y mueve PDFs a `processed_pdfs/`.
