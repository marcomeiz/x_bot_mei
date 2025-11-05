# Generation + Warden (Current State) — x_bot_mei

This document captures the up‑to‑date design and operating parameters for the single‑call variant generator (short/mid/long) with minimal Porter cleaning and strict validation delegated to an LLM Judge. Hard guardrails (Warden) are no longer enforced inside the generator.

## Overview

- Single LLM call returns three tweet variants: `short`, `mid`, `long` (JSON). Each string may contain line breaks.
- Voice: Alex Hormozi contract + Solopreneur‑in‑the‑Trenches ICP.
- Porter cleaning only: remove hashtags and collapse spaces per línea; NO validación mecánica.
- Validación estricta del contrato: realizada por el Juez LLM en `proposal_service.py` usando `prompts/validation/style_judge_v1.md`.
- Presets/temperature son configurables vía ENV.
- ChromaDB over HTTP con fallback; embeddings vía OpenRouter (SDK+HTTP) con parsing robusto.

## Guardrails Configuration (deprecado en el generador)

- Los defaults canónicos siguen en `config/warden.yaml` y los léxicos en `config/lexicon.json` para apoyar prompts y herramientas.
- El generador NO aplica estos guardrails durante el post‑proceso; solo limpia. La validación se hace con el Juez LLM.
- `STYLE_AUDIT_CONFIG_PATH` y variables `STYLE_*` gobiernan el auditor/guardian de estilo (fuera del generador).
- `ENFORCE_NO_COMMAS`, `ENFORCE_NO_AND_OR`, `WARDEN_WORDS_PER_LINE_*`, `MID_MIN/MAX`, `LONG_MIN/MAX` pueden usarse en utilidades como `tools/voice_check.py` y pruebas, pero no gatean la salida del generador.

## Environment (recommended defaults)

```
POST_PRESET=balanced
POST_TEMPERATURE=0.6

# Guardrails (opcionales, para herramientas de revisión; el generador no los aplica)
ENFORCE_NO_COMMAS=true
ENFORCE_NO_AND_OR=true
WARDEN_WORDS_PER_LINE_LO=5
WARDEN_WORDS_PER_LINE_HI=12
MID_MIN=180
MID_MAX=230
LONG_MIN=240
LONG_MAX=280

# Embeddings (use a stable, supported model for your key)
EMBED_MODEL=openai/text-embedding-3-large

# ChromaDB
CHROMA_DB_URL=http://<host>:<port>

# Optional tracing
LOG_WARDEN_FAILURE_REASON=true
```

## Prompt (v2.0)

- File: `prompts/generation/all_variants_v4.md`
- Key points:
  - Explicit rules in the prompt body (style/ICP/micro‑wins/saltos de línea).
  - JSON schema with multiline strings allowed. No prose in output.
  - The YAML front‑matter is informational; the model sees the prompt body.

## Presets & Temperature

- File: `src/settings.py`
- ENV:
  - `POST_PRESET=speed|balanced|quality` adjusts model + temperature.
  - `POST_TEMPERATURE` can override temperature directly.
- Wiring:
  - `core_generator.py` passes `settings.post_temperature` to `generate_all_variants`.
  - `variant_generators.py` uses `generation_temperature` when calling the LLM.

## Generation Flow (single‑call)

- File: `variant_generators.py` (function `generate_all_variants`)
- Messages:
  - System includes `<STYLE_CONTRACT>`, `<ICP>`, `<FINAL_REVIEW_GUIDELINES>`.
- User loads `prompts/generation/all_variants_v4.md`.
  - Reference voice anchors: injected via Style‑RAG RANDOM (`retrieve_goldset_examples_random(k=5)`) selecting 5 examples from the audited goldset v2.
- Steps:
  1) LLM returns `{short,mid,long}` (retry enforces schema if needed).
  2) Porter cleaning only (see below).
  3) Returns cleaned JSON; validation is deferred to the LLM Judge in `proposal_service.py`.

## Porter + Juez LLM (post‑process)

- Files: `variant_generators.py` (Porter cleaning), `proposal_service.py` (LLM Judge validation).
- Cleaning (Porter):
  - Remove hashtags y colapsa espacios por línea. Sin conversión de puntuación (no se alteran comas).
- Validación:
  - Estilo y mecánica se delegan al Juez LLM (`proposal_service.py`).
  - El juez evalúa cada variante contra `<STYLE_CONTRACT>` y devuelve booleanos estrictos para decidir el flujo.
- Config de guardrails:
  - Los toggles/rangos previos ya no se aplican aquí. Se mantienen para herramientas y experimentos.
- Logging:
  - Porter solo limpia; los rechazos/feedback provienen del flujo del Juez LLM.

## Embeddings

- File: `embeddings_manager.py`
- Client: OpenAI SDK (OpenRouter base_url + API key) + HTTP fallback.
- Robust parsing:
  - Accepts `data[0].embedding` or top‑level `embedding` if provider differs.
  - Circuit breaker (60s) after repeated errors.
- Recommended model (adjust if your key doesn’t support it):
  - `EMBED_MODEL=openai/text-embedding-3-large` (3072)
  - `GOLDSET_COLLECTION_NAME=goldset_norm_v1` + `GOLDSET_NORMALIZER_VERSION=1`

### Validación de contrato (Juez LLM) y uso del Goldset

- La validación de contrato/estilo de cada borrador ahora la realiza un Juez LLM dedicado.
  - Implementación: `ProposalService._check_contract_requirements` carga el prompt `prompts/validation/style_judge_v1.md` y llama a `_check_style_with_llm(draft)` por variante; devuelve `[bool, bool, bool]` y el control usa `all(results)`.
  - El goldset ya no se usa para bloquear en esta verificación; se mantiene para anclaje de estilo (Style‑RAG) y para telemetría al aprobar/publicar.
  - Recuperación de anchors para generación/comentarios: ACTUALMENTE se usa selección aleatoria `src/goldset.retrieve_goldset_examples_random(k=5)` (audited v2, 21 vectores). El método NN (`retrieve_goldset_examples_nn(query, k)`) permanece disponible pero no es el predeterminado.

> Cambio: Switch de NN a aleatorio (k=5) para anchors de Style‑RAG.
> Fecha: 2025-11-05
> Autor: Marco Mei
> Justificación: Con un goldset depurado (21 vectores), tomar 5 anchors aleatorios refuerza la voz doctrinal y reduce el sesgo del tópico.
  - Prompt `prompts/generation/all_variants_v4.md` acepta `{gold_examples_block}` como entrada directa desde `variant_generators`.
- Si por cualquier motivo el embedding falla y la similitud es `None`, no se bloquea por ese motivo (se mantiene la sugerencia), pero se recomienda revisar logs de `[EMB]`.

## ChromaDB

- File: `embeddings_manager.py`
- HTTP:
  - v2: `chromadb.HttpClient(host, port, ssl)`. Si la URL tiene subruta, el cliente ignora el path; expón Chroma en raíz.
  - lecturas: se solicitan solo `embeddings`/`metadatas` en `include` (no `ids`).
- Dependencies pinned:
  - `chromadb==0.4.24`, `numpy==1.26.4`.
- Performance: `find_relevant_topic` ≈ 0.27–5 s con HTTP.

### Persistencia local

- Si no hay `CHROMA_DB_URL`, se usa `db/` como directorio persistente local (en lugar de `/tmp/chroma`).

### Embedding policy for `/g`

- Estricto: antes de publicar la propuesta, se genera el embedding de cada variante A/B/C para medir similitud contra el goldset (impacta coste/latencia).
- Se registra en logs: `[GOLDSET] Draft <A|B|C> similarity=<score> (min=<umbral>)`.
- En la aprobación (A/B/C) se mantiene la generación de embedding del tweet aprobado para guardarlo en `memory_collection`.
- Enforcement points: `ProposalService._check_contract_requirements` → `_check_style_with_llm()` usando `prompts/validation/style_judge_v1.md`.

### Embeddings: fingerprint y dimensión

- El fingerprint de caché usa el modelo efectivo (incluye fallbacks) para evitar mezclar dimensiones.
- Si `SIM_DIM` está definida, se valida la dimensión antes de almacenar/reutilizar entradas; las incongruentes se ignoran.

### Firestore (embedding cache opcional)

- Estado: DESACTIVADO por defecto. Actívalo con `EMB_USE_FIRESTORE=1` si necesitas caché global de embeddings.
- Colección: `EMB_CACHE_COLLECTION` (por defecto `embedding_cache`). No debe contener `/`.
- Si `EMB_CACHE_COLLECTION` incluye `/`, el sistema lo sanea reemplazándolo por `_` y registra un warning.
- Los IDs de documento usan `fingerprint:model` y `key` saneados (se reemplaza `/` por `_`) para cumplir con Firestore.

## Tests

- File: `tests/test_variants_guardrails.py`
- Validates helpers for warden: one sentence per line, words/line range, english‑only, hedging/cliché/hype, char ranges, hashtags/conjunctions.

## Deployment

- Update environment variables (example):

```
gcloud run services update x-bot-mei \
  --region=europe-west1 \
  --update-env-vars \
  POST_PRESET=balanced,POST_TEMPERATURE=0.6,\
  ENFORCE_NO_COMMAS=true,ENFORCE_NO_AND_OR=true,\
  WARDEN_WORDS_PER_LINE_LO=5,WARDEN_WORDS_PER_LINE_HI=12,\
  MID_MIN=180,MID_MAX=230,LONG_MIN=240,LONG_MAX=280,\
  EMBED_MODEL=openai/text-embedding-3-large,\
  CHROMA_DB_URL=http://<host>:<port>,\
  LOG_WARDEN_FAILURE_REASON=true
```

## Smoke Test (3 topics)

1) `Client chaos: no invoicing rule` — Expect staccato cadence + micro‑win (invoice rule → auto send).
2) `Onboarding: too many DMs and no system` — Expect single intake form → auto‑tag → queue.
3) `Pricing floor: stop custom quotes` — Expect set floor price → publish → faster “no”.

Validation for each: `short ≤160`, `mid 180–230`, `long 240–280`; one sentence per line; 5–12 words per line; no commas/and‑or; english‑only; no hedging/cliché/hype; visible micro‑win.

## Troubleshooting

- Generation too slow:
  - Use `POST_PRESET=speed` or lower `POST_TEMPERATURE=0.55–0.6`.
- Warden rejects most drafts:
  - Inspect `WARDEN_FAIL_REASON`; adjust prompt or relax `ENFORCE_NO_AND_OR=false` (≤1 conjunción/línea si habilitas).
- Embeddings errors (`missing data array`/`circuit open`):
  - Fix `EMBED_MODEL` to a provider‑supported model; verify logs show a numeric vector.
- Topic selection slow:
  - Ensure `CHROMA_DB_URL` is set and using HTTP (not FS/GCSFuse).

## Definition of Done

- 3 smoke tests pass without `WARDEN_FAIL_REASON`.
- `[PERF] Single‑call` in 20–45 s (balanced) or 15–30 s (speed).
- No embedding errors; similarity working after a few approvals.
- Outputs are human, imperative, tactical, ready to publish.
## Health de embeddings

- Endpoint: `/health/embeddings`
- Campos:
  - `goldset_dim`, `topics_dim`, `memory_dim`: longitud de un vector representativo por colección.
  - `goldset_dims_seen`, `topics_dims_seen`, `memory_dims_seen`: conjunto de dimensiones observadas (muestra ≤200).
  - `sim_dim_env`: valor de `SIM_DIM` si está definido.
  - `agree_dim`: true si todas las colecciones comparten dimensión y coincide con `SIM_DIM` (si existe).
  - `warmed`: cantidad de anclas precalentadas; `ok=true` exige `warmed >= WARMUP_ANCHORS` y `agree_dim=true`.
## Métricas de latencia (/g)

- Formato: logs `[METRIC]` con nombre y valor en ms.
- Principales métricas añadidas:
  - `g_find_topic`: selección de tema en Chroma.
  - `g_goldset_retrieve`: carga de anchors/ejemplos del goldset.
  - `g_llm_single_call`: llamada LLM que genera A/B/C.
  - `g_generate_variants`: envoltura de generación (desde propuesta).
  - `g_check_contract_style_llm`: verificación de contrato/estilo vía Juez LLM.
  - `g_send_proposal`: envío del mensaje de propuesta a Telegram.
  - `g_embed_memory_on_approval`, `g_memory_add`: embedding y persistencia al aprobar.
  - `g_send_*`: tiempos de mensajes de aviso/errores y prompts de publicación.

Nota de control (2025-11-05):
- Cuando el Juez LLM devuelve true para todas las variantes presentes, el sistema considera éxito y evita el mensaje de reintento de contrato.
- Implementación: `ProposalService.propose_tweet` calcula `all_passed_pre/post = all(check_results)` sobre las variantes presentes y procede al envío en caso afirmativo.

## Resumen de variantes en logs

- Al final de la propuesta, se emite un bloque `[SUMMARY]` con cada variante y su similitud con el goldset:
  - Formato: `- A | sim=0.468 | <texto>` (sim con 3 decimales, `NA` si no se pudo calcular).
  - Fuente: `ProposalService` tras calcular contrato+goldset.
## Juez-Calificador (Grader) — 2025-11-05

- Se reemplaza el juez binario por un Grader JSON (archivo `prompts/validation/style_judge_v1.md`).
- El Grader devuelve `cumple_contrato` y un `razonamiento_principal` breve con referencias al pilar del contrato que falla, más puntajes `tono/diccion/ritmo`.
- `proposal_service._check_style_with_llm` registra estos datos en `variant_evaluation` mediante `diagnostics_logger.log_post_metrics`.
- Justificación: el flujo anterior no ofrecía motivos de fallo, dificultando depuración y ajuste fino de estilo.
