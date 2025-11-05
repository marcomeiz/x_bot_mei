# Long‑First Pipeline (v1)

Propósito: Minimizar latencia total y mantener coherencia de voz generando primero una sola variante LONG de máxima calidad, evaluándola temprano con el Judge, y derivando MID y SHORT exclusivamente desde el LONG aprobado.

Fecha: 2025-11-05
Autor: AI assistant
Justificación: El flujo secuencial tradicional genera y evalúa múltiples variantes en paralelo, lo que aumenta latencia y costo. Con evaluación temprana del LONG, se evita gasto innecesario cuando el contenido base falla; y al derivar MID/SHORT desde LONG se preserva coherencia.

## Estados del flujo

1. LONG_GENERATE
- Generar exactamente 1 versión LONG (240–280 chars) con `GenerationSettings` de calidad.
- API: `regenerate_single_variant('long', topic_abstract, context, settings)`.

2. LONG_EVAL
- Evaluación temprana con Judge LLM (`evaluation.evaluate_draft`).
- Criterio de FAIL: no alcanzar el umbral (`LONG_CONFIDENCE_THRESHOLD` o base `EVAL_CONFIDENCE_THRESHOLD`).
- Caché: `eval_cache` reutiliza evaluaciones positivas por texto.
- Circuit breaker: si falla, aborta el pipeline.

3. VARIANTS_FROM_LONG
- Derivación paralela de MID (140–230 chars) y SHORT (≤140 chars) usando el mismo modelo refiner (`POST_REFINER_MODEL`).
- APIs: `compress_to_mid(long_text, model)` y `compress_to_short(long_text, model)`.
- Ambas variantes deben basarse estrictamente en el LONG aprobado.

4. VARIANT_EVAL
- Evaluación independiente para MID y SHORT con el Judge.
- Umbrales de calidad adaptados por formato (ENV `MID_CONFIDENCE_THRESHOLD`, `SHORT_CONFIDENCE_THRESHOLD`).
- Fallback: Si una variante falla, conservar la otra si aprueba.

## Requisitos técnicos implementados

- Caché de evaluaciones: `eval_cache.py` (TTL configurable `EVAL_CACHE_TTL_SECONDS`, almacenamiento en `.cache/eval_cache.json`). Guarda solo aprobaciones.
- Monitoreo de métricas: `diagnostics_logger` emite eventos por etapa y latencias (`stage_latencies`).
- Configuración adaptable: umbrales por variante vía ENV; modelo generador y refiner desde `src/settings.AppSettings`.
- Circuit breaker: aborta tras `LONG_EVAL` si no alcanza el umbral.

## APIs principales

- `long_first_pipeline.run_long_first_pipeline(topic_abstract: str, context: PromptContext) -> Dict[str, Any]`
  - Campos: `long`, `mid`, `short`, `stage_latencies`, `evaluations`, `errors`, `pipeline_version`.

## Notas de integración

- El pipeline no cambia las APIs públicas en `core_generator.py`; puede invocarse desde servicios o pruebas E2E.
- Las variantes derivadas usan `ensure_char_range_via_llm` internamente para respetar los rangos de caracteres.
- `PIPELINE_VERSION` se fija a `long_first_v1` por defecto para trazabilidad en logs.

## Registro de cambios

- Cambio: creación del motor de estados long‑first (`long_first_pipeline.py`).
- Fecha: 2025-11-05
- Autor: AI assistant
- Justificación: reducción de latencia con evaluación temprana, coherencia por derivación, control de calidad adaptativo.
