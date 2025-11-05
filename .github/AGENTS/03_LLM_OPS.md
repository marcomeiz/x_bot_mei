# 03 · LLM Ops (políticas, prompts, fallback, evaluación)

## Proveedor (OpenRouter‑only)
- Proveedor único: OpenRouter (`OPENROUTER_API_KEY`).
- Modelos baratos por defecto:
  - Post: `qwen/qwen-2.5-7b-instruct`
  - Eval (fast): `qwen/qwen-2.5-7b-instruct`
  - Eval (slow): `mistralai/mistral-nemo`
  - Audit comentarios: `qwen/qwen-2.5-7b-instruct`
  - Rewrite comentarios: `mistralai/mistral-nemo`
  - Tópicos: `mistralai/mistral-nemo`
  - Embeddings: `openai/text-embedding-3-small` (HTTP-first)
- JSON estricto: si el modelo no soporta `response_format`, se hace fallback a parseo robusto.

## Embeddings (HTTP-first + fallback + circuito)
- Ruta primaria: HTTP directo a `{OPENROUTER_BASE_URL}/embeddings` con `{"model","input"}`.
- Si la respuesta no incluye `data[0].embedding`, se prueban candidatos alternativos (en orden):
  - `openai/text-embedding-3-small`, `thenlper/gte-small`, `jinaai/jina-embeddings-v2-base-en`.
- Si uno responde bien, se conmute dinámicamente el modelo efectivo para siguientes llamadas.
- Circuit breaker: ante errores, se evita reintentar durante 60s (protege costes y latencia).
- Logs: mensajes claros `Embeddings HTTP error …` o `Embedding HTTP response missing data array` solo en caso de fallo real del proveedor.

## Generación (A/B/C)
- Prompt único con contrato, ICP y guías finales.
- Guardrails: sin comas; sin “and/or”/“y/o”; sin `-mente`/`-ly`; ≤280; hooks; formatos (staccato, stair up/down, list strikes).
- Variantes: `short` (~<=140), `mid` (~140–230), `long` (~240–280).
- Validación y refinado: si viola reglas/longitud, se corrige vía LLM; nunca truncado local.

## Evaluación (dos jueces)
- Rápido (flash): criterios objetivos en `config/evaluation_fast.yaml`.
- Lento (pro): subjetivos en `config/evaluation_slow.yaml` si la confianza es baja.
- System: JSON estricto + contrato + ICP.

## Auditoría de comentarios (/c)
- Protocolo “Accept and Connect” v4.0; respuesta JSON con `is_compliant` y posible `corrected_text`.
- Menos de 140 caracteres en comentario final.
