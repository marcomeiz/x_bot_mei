# Informe de Estado y Diagnóstico Técnico

## 1. Estado Actual del Sistema

*   **Entorno Local:** El sistema es **100% funcional**. El ciclo completo de ingesta de PDFs (`run_watcher.py`) y la consulta de datos a través de la API del bot (`bot.py`) operan según lo esperado.
*   **Entorno de Producción (GCP):** El sistema **no es funcional**. Las peticiones a los endpoints del bot que requieren acceso a la base de datos fallan.

## 2. Historial de Arquitecturas y Acciones

Esta sección detalla cronológicamente las arquitecturas implementadas y las acciones realizadas en el entorno de producción.

### 2.1. Arquitectura 1: Cloud Run + GCS FUSE

*   **Diseño:**
    *   El bot (`x-bot-mei`) se ejecuta como un servicio de Cloud Run.
    *   La base de datos ChromaDB (`x-bot-db`) se ejecuta como un segundo servicio de Cloud Run.
    *   La persistencia de la base de datos se intentó lograr montando un bucket de Google Cloud Storage (GCS) como un volumen del sistema de archivos a través de GCS FUSE.
*   **Observación:** La ingesta de datos y las operaciones de lectura fallaban de forma intermitente.
*   **Evidencia (Logs):**
    *   `chromadb.errors.InternalError: ... no such table: embeddings`
    *   `chromadb.errors.InternalError: ... mismatched types; Rust type 'u64' (as SQL type 'INTEGER') is not compatible with SQL type 'BLOB'`
    *   `BufferedWriteHandler.OutOfOrderError for object: chroma.sqlite3-journal...`
*   **Conclusión Factual:** Las operaciones de escritura de bajo nivel de SQLite (usado por ChromaDB) son incompatibles con la capa de abstracción de GCS FUSE, llevando a la corrupción de la base de datos.

### 2.2. Arquitectura 2: Cloud Run + VM (IP Pública)

*   **Diseño:**
    *   Se abandonó GCS FUSE.
    *   Se provisionó una VM de Compute Engine (`chroma-vm`) para alojar el servidor ChromaDB en un contenedor Docker, usando el disco local de la VM para la persistencia.
    *   El bot en Cloud Run (`x-bot-mei`) fue configurado para conectarse a la **IP pública** de la VM.
*   **Observación:** Las peticiones desde el bot al servidor de la base de datos fallaban.
*   **Evidencia (Logs):**
    *   `requests.exceptions.ConnectTimeout: HTTPConnectionPool(host='...', port=8080): Max retries exceeded... (Caused by ConnectTimeoutError(... 'Connection timed out...'))`
*   **Conclusión Factual:** La conexión de red desde el entorno de Cloud Run a la IP externa de una VM en el mismo proyecto no funciona con la configuración de red por defecto.

### 2.3. Arquitectura 3 (Actual): Cloud Run + VM (IP Privada vía VPC Connector)

*   **Diseño:**
    *   Se creó una nueva red VPC (`chroma-net`).
    *   Se creó un **Conector de Acceso a VPC sin Servidor** (`chroma-connector`) para unir Cloud Run a la red `chroma-net`.
    *   La VM (`chroma-vm`) fue recreada dentro de la red `chroma-net`.
    *   El bot en Cloud Run (`x-bot-mei`) fue reconfigurado para usar el VPC Connector y apuntar a la **IP privada** de la VM.
*   **Observación:** Las peticiones desde el bot al servidor de la base de datos siguen fallando.
*   **Evidencia (Logs del Bot - `x-bot-mei`):**
    *   `chromadb.errors.InternalError: ... mismatched types; Rust type 'u64' (as SQL type 'INTEGER') is not compatible with SQL type 'BLOB'`
*   **Conclusión Factual:** La conexión de red ahora se establece correctamente (el error ya no es `Connection timed out`). Sin embargo, el error de `mismatched types` ha reaparecido, indicando una inconsistencia entre los datos escritos y los datos leídos.

## 3. Problemas de Despliegue y Build

Paralelamente a los problemas de arquitectura, se han identificado fallos en el proceso de construcción y despliegue:

*   **Inconsistencia de Dependencias:** Se ha observado que el entorno de construcción de Cloud Build no siempre instalaba las versiones de las librerías especificadas en `requirements.txt`, sino que usaba versiones cacheadas. Esto provocó incompatibilidades entre el cliente (bot) y el servidor (base de datos).
    *   **Acción de Mitigación:** Se añadió el flag `--no-cache` al `Dockerfile` y posteriormente se cambió a un `cloudbuild.yaml` que usa buildpacks nativos para forzar una instalación limpia de dependencias.
*   **Fallo del Trigger de CI/CD:** Se verificó que el `git push` a la rama `main` no está disparando automáticamente un nuevo build, indicando un problema con la configuración del trigger de Cloud Build.
*   **Fallo del Build por Timeout:** El último intento de despliegue con `cloudbuild.yaml` y buildpacks falló por `TIMEOUT`.
    *   **Hipótesis:** La instalación de todas las dependencias desde cero, incluso las de runtime, supera el tiempo de espera por defecto de Cloud Build (10 minutos).
    *   **Acción Propuesta (No Ejecutada):** Separar `requirements.txt` en `requirements.runtime.txt` (para producción) y `requirements.dev.txt` (para desarrollo local) para reducir el tiempo de construcción.

## 4. Estado Final de la Evidencia

*   **El código en la rama `main` de GitHub es funcional en un entorno local.**
*   La arquitectura de red actual (VPC Connector) es la recomendada por Google y la conexión a nivel de red parece funcionar.
*   El problema inmediato que impide el progreso es que el proceso de **build en la nube falla por `TIMEOUT`**, lo que nos impide desplegar la última versión del código del bot con las dependencias correctas.
*   El error `mismatched types` es un **síntoma** de la incompatibilidad de versiones causada por el fallo en el despliegue.