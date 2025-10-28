# 03 · LLM Ops (políticas, prompts, fallback, evaluación)

## Fallback
- Orden por defecto: `FALLBACK_PROVIDER_ORDER=gemini,openrouter`.
- Gemini: requiere `GOOGLE_API_KEY` y `GEMINI_MODEL`.
- OpenRouter: requiere `OPENROUTER_API_KEY`. Si se cae Gemini y el `model` empieza por `gemini-`, se usa `OPENROUTER_DEFAULT_MODEL`.
- Responder SIEMPRE JSON estricto en generación y evaluación.

## Generación (A/B/C)
- Prompt único con contrato, ICP y guías finales.
- Guardrails: sin comas; sin “and/or”/“y/o”; sin `-mente`/`-ly`; ≤280; hooks; formatos (staccato, stair up/down, list strikes).
- Variantes: `short` (~<=160), `mid` (~180–230), `long` (~240–280).
- Validación y refinado: si viola reglas/longitud, se corrige vía LLM; nunca truncado local.

## Evaluación (dos jueces)
- Rápido (flash): criterios objetivos en `config/evaluation_fast.yaml`.
- Lento (pro): subjetivos en `config/evaluation_slow.yaml` si la confianza es baja.
- System: JSON estricto + contrato + ICP.

## Auditoría de comentarios (/c)
- Protocolo “Accept and Connect” v4.0; respuesta JSON con `is_compliant` y posible `corrected_text`.
- Menos de 140 caracteres en comentario final.

