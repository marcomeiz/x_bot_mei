# x_bot_mei

**Estado del Sistema: Estable y Optimizado (Octubre 2025)**

## Resumen Ejecutivo

Este proyecto genera contenido para redes sociales (tweets) a partir de documentos (PDFs) y otras fuentes de datos. El sistema ha sido sometido a una refactorización profunda para resolver problemas críticos de rendimiento y estabilidad.

La arquitectura actual se basa en los siguientes principios:

1.  **Eficiencia:** Se ha optimizado el proceso de generación para minimizar la latencia, pasando de ~10 minutos a **~90 segundos** por ejecución completa.
2.  **Calidad y Variedad:** La generación de contenido se realiza en una única llamada a un LLM, instruyéndole para que produzca múltiples variantes (corta, media, larga) y realice un proceso de auto-crítica interno. Esto mantiene la calidad y variedad sin sacrificar el rendimiento.
3.  **Evaluación Adaptativa:** Se utiliza un sistema de evaluación de dos jueces (rápido y lento) para auditar la calidad de los borradores de forma eficiente, usando modelos de LLM más potentes solo cuando es necesario.
4.  **Configuración Externalizada:** Las rúbricas de evaluación y otros parámetros de configuración se gestionan en ficheros YAML dentro de la carpeta `config/`, permitiendo ajustes rápidos sin necesidad de redesplegar el código.

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

## Instalación y Ejecución

La instalación y ejecución no han cambiado, pero se documentan las variables relevantes del motor de fallback.

1.  **Instalar:** `python -m venv venv && source venv/bin/activate && pip install -r requirements.txt`.
2.  **Variables (.env) — claves y modelos**
    - `OPENROUTER_API_KEY` (obligatoria)
- `OPENROUTER_BASE_URL` (por defecto `https://openrouter.ai/api/v1`)
- `POST_MODEL` (por defecto `x-ai/grok-3`), `EVAL_FAST_MODEL`, `EVAL_SLOW_MODEL`
- `POST_REFINER_MODEL` (por defecto reaprovecha `POST_MODEL`)
- `POST_PRESET` (`speed|balanced|quality`) y/o `POST_TEMPERATURE`
- `JOB_MAX_WORKERS` (por defecto `3`), `JOB_QUEUE_MAXSIZE` (por defecto `12`), `JOB_TIMEOUT_SECONDS` (por defecto `35`)
- `LLM_REQUEST_TIMEOUT_SECONDS` (por defecto `15`), `LLM_MIN_WINDOW_SECONDS` (por defecto `5`)
3.  **Embeddings (HTTP-first)**
    - `EMBED_MODEL` (por defecto `jinaai/jina-embeddings-v2-base-en`)
    - Llamada HTTP directa con fallback a SDK; circuito de 60s tras errores.
4.  **ChromaDB**
    - `CHROMA_DB_URL` para cliente HTTP (recomendado) o `CHROMA_DB_PATH` para local.
5.  **Ingesta local:** `python run_watcher.py` y copiar PDFs a `uploads/`.
6.  **Bot:** `/g` para propuestas (A/B/C) y `/c <texto>` para comentar.
7.  **Generación manual (debug):** `python -i core_generator.py` y ejecutar `generate_tweet_from_topic("<abstract>")`.
