# Generation + Warden (Current State) — x_bot_mei

This document captures the up‑to‑date design and operating parameters for the single‑call variant generator (short/mid/long) with hard guardrails (Warden), so future sessions can continue seamlessly.

## Overview

- Single LLM call returns three tweet variants: `short`, `mid`, `long` (JSON). Each string may contain line breaks.
- Voice: Alex Hormozi contract + Solopreneur‑in‑the‑Trenches ICP + Final Review (Warden).
- Hard “Porter” layer cleans and validates drafts (rejects or compacts) before Telegram.
- Presets/temperature are configurable via ENV.
- ChromaDB over HTTP with v2→v1 fallback; embeddings via OpenRouter (SDK+HTTP) with robust parsing.

## Environment (recommended defaults)

```
POST_PRESET=balanced
POST_TEMPERATURE=0.6

# Guardrails
ENFORCE_NO_COMMAS=true
ENFORCE_NO_AND_OR=true
WARDEN_WORDS_PER_LINE_LO=5
WARDEN_WORDS_PER_LINE_HI=12
MID_MIN=180
MID_MAX=230
LONG_MIN=240
LONG_MAX=280

# Embeddings (use a stable, supported model for your key)
EMBED_MODEL=jinaai/jina-embeddings-v2-base-en

# ChromaDB
CHROMA_DB_URL=http://<host>:<port>

# Optional tracing
LOG_WARDEN_FAILURE_REASON=true
```

## Prompt (v2.0)

- File: `prompts/generation/all_variants.md`
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
  - User loads `prompts/generation/all_variants.md`.
- Steps:
  1) LLM returns `{short,mid,long}` (retry enforces schema if needed).
  2) Warden cleaning + validation (see below).
  3) Returns cleaned JSON; otherwise raises `StyleRejection` (regenerate upstream).

## Warden / Porter (post‑process)

- Files: `variant_generators.py` (cleaning + warden checks), `writing_rules.py` (mechanical checks, anti‑AI patterns), `style_guard.py` (improve_style/audit).
- Cleaning:
  - Remove hashtags, convert commas to dot, collapse spaces.
- Improve & audit:
  - `improve_style(draft, contract)` then re‑clean.
- Mechanical checks (writing_rules)
  - Detect & reject: hashtags, commas, `and/or` (configurable), conjunctions; banned suffixes; AI‑like phrases.
- Warden hard checks:
  - English‑only (no ES diacritics).
  - No hedging/cliché/hype (regex).
  - One sentence per line; each line ends with `. ! ?`.
  - 5–12 words per line (configurable with `WARDEN_WORDS_PER_LINE_LO/HI`).
  - Character ranges per variant: `short ≤160`, `mid 180–230`, `long 240–280` (configurable).
  - If fails: try compaction ≤280 with `ensure_under_limit_via_llm`; re‑validate; otherwise `StyleRejection`.
- Toggles:
  - `ENFORCE_NO_COMMAS`, `ENFORCE_NO_AND_OR` (can relax later for CTR testing).
- Logging:
  - When rejecting, logs `WARDEN_FAIL_REASON=<reason>`.

## Embeddings

- File: `embeddings_manager.py`
- Client: OpenAI SDK (OpenRouter base_url + API key) + HTTP fallback.
- Robust parsing:
  - Accepts `data[0].embedding` or top‑level `embedding` if provider differs.
  - Circuit breaker (60s) after repeated errors.
- Recommended model (adjust if your key doesn’t support it):
  - `EMBED_MODEL=jinaai/jina-embeddings-v2-base-en`

## ChromaDB

- File: `embeddings_manager.py`
- HTTP with fallback:
  - v2: `chromadb.HttpClient(host, port, ssl)`; if 404 on `/api/v2`, fallback v1: `chromadb.Client(Settings(chroma_api_impl="rest", ...))`.
- Dependencies pinned:
  - `chromadb==0.4.24`, `numpy==1.26.4` (compatibility for REST v1; avoids `np.float_` error).
- Performance: `find_relevant_topic` ≈ 0.27–5 s with HTTP.

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
  EMBED_MODEL=jinaai/jina-embeddings-v2-base-en,\
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

