# x_bot_mei

**Estado del Sistema: Estable y Optimizado (Octubre 2025)**

## Resumen Ejecutivo

Este proyecto genera contenido para redes sociales (tweets) a partir de documentos (PDFs) y otras fuentes de datos. El sistema ha sido sometido a una refactorización profunda para resolver problemas críticos de rendimiento y estabilidad.

La arquitectura actual se basa en los siguientes principios:

1.  **Eficiencia:** Se ha optimizado el proceso de generación para minimizar la latencia, pasando de ~10 minutos a **~90 segundos** por ejecución completa.
2.  **Calidad y Variedad:** La generación de contenido se realiza en una única llamada a un LLM, instruyéndole para que produzca múltiples variantes (corta, media, larga) y realice un proceso de auto-crítica interno. Esto mantiene la calidad y variedad sin sacrificar el rendimiento.
3.  **Evaluación Adaptativa:** Se utiliza un sistema de evaluación de dos jueces (rápido y lento) para auditar la calidad de los borradores de forma eficiente, usando modelos de LLM más potentes solo cuando es necesario.
4.  **Configuración Externalizada (Hard No a hardcode):** Nada que pueda vivir en `config/`, `.env` o prompts se deja incrustado en el código. Cada ajuste pasa por archivos versionables o variables de entorno para mantener el sistema auditable y tunable sin redeploys.

## Arquitectura Actual

-   **Generación de Variantes (`variant_generators.py`):**
    -   La función `generate_all_variants` es ahora el corazón del sistema. Utiliza un único prompt de "Chain of Thought" para instruir al LLM a realizar el `tail-sampling`, el `debate interno` y la generación de 3 variantes de distinta longitud en una sola llamada.
-   **Evaluación (`evaluation.py`):**
    -   El sistema utiliza un flujo de dos pasos:
        1.  **Juez Rápido:** Un modelo de LLM rápido (`gemini-1.5-flash`) evalúa criterios cuantificables definidos en `config/evaluation_fast.yaml`.
        2.  **Juez Sabio:** Si la puntuación del juez rápido no supera un umbral de confianza (`EVAL_CONFIDENCE_THRESHOLD`), un modelo más potente (`gemini-1.5-pro`) evalúa los criterios subjetivos definidos en `config/evaluation_slow.yaml`.
-   **Orquestación (`core_generator.py` y `proposal_service.py`):**
    -   Estos módulos han sido actualizados para llamar a la nueva función de generación unificada y manejar la nueva estructura de datos.

## Siguientes Pasos y Mejoras Pendientes ("Fase 2")

1.  **Optimización de la Base de Datos:** El cuello de botella principal que queda es la búsqueda de temas en ChromaDB (~20 segundos), causado por la arquitectura de GCS FUSE. La solución definitiva es migrar a un servicio de base de datos vectorial gestionado (ej. Pinecone, Weaviate, Vertex AI Vector Search).
2.  **Implementar "Golden Set" de Calidad:** Crear un conjunto de casos de prueba con resultados ideales para validar automáticamente la calidad de la generación tras futuros cambios en los prompts.
3.  **Monitorización y Alertas:** Configurar dashboards y alertas en Google Cloud para monitorizar la latencia, el ratio de errores y los costes de forma proactiva.

## Documentación de Generación + Warden

- Guía completa de guardrails, presets, prompt v2.0, validadores y despliegue: ver `docs/GENERATION_WARDEN.md`.
- Telemetría a prueba de fallos: contrato y política de errores en `docs/ops/telemetry.md`.

## Instalación y Ejecución

La instalación y ejecución no han cambiado, pero se documentan las variables relevantes del motor de fallback.

1.  **Instalar:** `python -m venv venv && source venv/bin/activate && pip install -r requirements.txt`.
2.  **Variables (.env) — claves y modelos**
    - `OPENROUTER_API_KEY` (obligatoria)
- `OPENROUTER_BASE_URL` (por defecto `https://openrouter.ai/api/v1`)
- `POST_MODEL` (por defecto `x-ai/grok-4-fast`), `EVAL_FAST_MODEL`, `EVAL_SLOW_MODEL`
- `POST_REFINER_MODEL` (por defecto reaprovecha `POST_MODEL`)
- `POST_PRESET` (`speed|balanced|quality`) y/o `POST_TEMPERATURE`
- `JOB_MAX_WORKERS` (por defecto `3`), `JOB_QUEUE_MAXSIZE` (por defecto `12`), `JOB_TIMEOUT_SECONDS` (por defecto `35`)
- `LLM_REQUEST_TIMEOUT_SECONDS` (por defecto `15`), `LLM_MIN_WINDOW_SECONDS` (por defecto `5`)
- `GOLDSET_COLLECTION_NAME` (por defecto `goldset_norm_v1`) y `GOLDSET_NORMALIZER_VERSION` (por defecto `1`) para alinear embeddings normalizados del goldset.
- `TELEMETRY_STRICT` (por defecto `0`): controla la política de fallos del wrapper de telemetría (`safe_capture`).
3.  **Bootstrap local:** `python scripts/bootstrap_workspace.py --clean` para regenerar `json/`, `texts/` y `uploads/` con semillas reproducibles.
4.  **Selección de configuración (`config/settings.<env>.yaml`):**
    - `APP_CONFIG_ENV=dev|prod` (por defecto `dev`) selecciona el YAML bajo `config/`.
    - `APP_CONFIG_PATH=/ruta/personalizada/settings.yaml` sobrescribe el archivo directamente.
    - Variables de entorno individuales siguen teniendo prioridad sobre lo que declare el YAML.
4.  **Guardrails (`config/warden.yaml`, `config/lexicon.json`, `config/style_audit.yaml`):** Define los límites duros del Warden (commas, palabras por línea, rangos de caracteres, modo minimal), el vocabulario vetado/stopwords y los umbrales del guardián de estilo. Usa `WARDEN_CONFIG_PATH`, `LEXICON_CONFIG_PATH` o `STYLE_AUDIT_CONFIG_PATH` para apuntar a otras rutas, o variables (`ENFORCE_NO_COMMAS`, `STYLE_MEMORY_SIMILARITY_FLOOR`, etc.) si necesitas overrides rápidos.
5.  **Mensajes (`config/messages.yaml`):** Copys para Telegram (bot, propuestas, avisos). Usa `MESSAGES_CONFIG_PATH` o overrides puntuales por env si hace falta.
6.  **Embeddings (HTTP-first)**
    - `EMBED_MODEL` (por defecto `openai/text-embedding-3-small` desde `config/settings.*.yaml`)
    - Recomendado en producción: `openai/text-embedding-3-large` (3072) para compatibilidad con `SIM_DIM=3072` y pipelines (`cloudbuild_reembed.yaml`).
    - Llamada HTTP directa con fallback a SDK; circuito de 60s tras errores.
7.  **ChromaDB**
    - `CHROMA_DB_URL` para cliente HTTP (recomendado) o `CHROMA_DB_PATH` para local.
8.  **Ingesta local:** `python run_watcher.py` y copiar PDFs a `uploads/`.
9.  **Bot:** `/g` para propuestas (A/B/C) y `/c <texto>` para comentar.
10. **Generación manual (debug):** `python -i core_generator.py` y ejecutar `generate_tweet_from_topic("<abstract>")`.
11. **Higiene del repo:** `python scripts/check_repo_hygiene.py` valida que no se hayan versionado logs, caches o secretos antes de hacer push.

## Scripts Utilitarios Nuevos

- `scripts/bootstrap_workspace.py`: prepara carpetas ignoradas (`json/`, `texts/`, `uploads/`) y copia semillas deterministas desde `data/seeds/`. Útil al clonar o reiniciar el entorno local.
- `scripts/check_repo_hygiene.py`: bloquea la versión accidental de artefactos temporales. Está integrado en CI (`.github/workflows/hygiene.yml`) y puede ejecutarse localmente antes de cada commit.

Las semillas incluidas (`data/seeds/topics_sample.jsonl`, `data/seeds/text_sample.txt`) permiten smoke tests sin depender de PDFs reales.

> Consulta `docs/workspace_bootstrap.md` para un desglose paso a paso del bootstrap e higiene.

## Diagnóstico de propuestas que no cumplen umbrales

Para observar y analizar por qué ciertos borradores no alcanzan los criterios, el servicio emite logs estructurados (JSON) con eventos `EVAL_METRICS` y `EVAL_FAILURE`.

Qué se registra:
- Contenido completo de cada variante (A/B/C)
- Métricas por variante: longitud, palabras, comas, voz en 2ª persona, similitud al goldset
- Resultados de evaluación rápida/lenta (style_score, clarity_score, contrarian_score, etc.)
- Umbrales vigentes (p. ej., `GOLDSET_MIN_SIMILARITY`, `VARIANT_SIMILARITY_THRESHOLD`, `STYLE_*`)
- Bloqueos y motivo (`blocking_reason`), incluyendo similitud entre variantes

Filtros útiles (Cloud Logging):
```
resource.type="cloud_run_revision" AND 
resource.labels.service_name="x-bot-mei" AND 
timestamp>="-PT30M" AND 
textPayload:"\"event\": \"EVAL_"
```

Script de análisis:
```
python scripts/analyze_failure_logs.py --use-gcloud --project xbot-473616 --service x-bot-mei --minutes 60
```
O bien pasar logs por stdin:
```
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=x-bot-mei AND timestamp>=-PT60M AND textPayload:\"\\\"event\\\": \\\"EVAL_\\\"" --project xbot-473616 --format value(textPayload) | \
python scripts/analyze_failure_logs.py
```

Configuración por entorno (calibración):
- `VARIANT_SIMILARITY_THRESHOLD` (por defecto 0.78)
- `UMBRAL_SIMILITUD` / `GOLDSET_MIN_SIMILITUD` (por defecto 0.75)
- `ENFORCE_NO_COMMAS` (`1`/`0`)
- `STYLE_MEMORY_SIMILARITY_FLOOR`, `STYLE_HEDGING_THRESHOLD`, `STYLE_JARGON_BLOCK_THRESHOLD`
