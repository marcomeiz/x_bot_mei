**Guía Rápida (4 pasos)**
- 1) Instalar: `python -m venv venv && source venv/bin/activate && pip install -r requirements.txt`.
- 2) Configurar `.env`: define `GOOGLE_API_KEY`, opcional `OPENROUTER_API_KEY`, y deja `FALLBACK_PROVIDER_ORDER=gemini,openrouter`, `GEMINI_MODEL=gemini-2.5-pro`.
 - 3) Iniciar watcher: `python watcher_with_metadata.py` y copia un PDF a `uploads/`. Se extraen, validan y guardan temas en `db/` + `json/`.
- 4) Generar tweets: `python -i core_generator.py` y ejecuta `generate_tweet_from_topic("<abstract>")`.

**Resumen**
- Genera ideas de tweets a partir de PDFs, valida su relevancia para un perfil COO y almacena los temas en una base vectorial (ChromaDB).
- A partir de un tema, crea dos alternativas de tweet refinadas y bajo 280 caracteres.
- Usa una capa LLM con fallback automático: intenta OpenRouter y, si no hay créditos o hay error, continúa con Gemini.

**Componentes**
- `llm_fallback.py`: capa común para LLM con orden de proveedores y manejo de JSON.
- `embeddings_manager.py`: embeddings con Google Generative AI (`models/embedding-001`) y cliente persistente de ChromaDB.
- `watcher_with_metadata.py`: observa `uploads/`, extrae texto de PDFs, valida temas y añade metadatos (por ejemplo el nombre del PDF) para trazabilidad y mejor deduplicación.
- `core_generator.py`: dado un tema, genera dos borradores `[EN - A]` y `[EN - B]`, los refina y asegura < 280 caracteres.
- `offline_generate.py`: genera 2 variantes de tweet sin LLM a partir de un tema aleatorio (útil para pruebas rápidas).
- `bot.py`: utilidades para enviar/editar mensajes por Telegram (opcional).
- `logger_config.py`: logging centralizado a stdout.

**Requisitos**
- Python 3.10 o superior.
- macOS/Linux recomendado (usa `desktop-notifier` y `watchdog`).
- Dependencias: `pip install -r requirements.txt`.

**Configuración (.env)**
- Variables obligatorias:
  - `OPENROUTER_API_KEY` (si usas OpenRouter)
  - `GOOGLE_API_KEY` (requerido para embeddings y fallback Gemini)
- Variables recomendadas para el fallback:
  - `FALLBACK_PROVIDER_ORDER` (por defecto `gemini,openrouter`)
  - `GEMINI_MODEL` (por defecto `gemini-2.5-pro`)
- Otras (si usas integraciones):
  - `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
  - Credenciales de X/Twitter si se usan scripts relacionados
 - Visualización opcional:
   - `SHOW_TOPIC_ID` (`0` por defecto). Si `1`, muestra el ID de tema en el encabezado del mensaje del bot aun cuando exista `Origen`. Por defecto el ID queda oculto salvo que no haya `Origen`.
- Estilo (opcional):
   - `ENFORCE_STYLE_AUDIT` (`1` por defecto): activa la auditoría de estilo y revisión condicional.
   - `STYLE_REVISION_ROUNDS` (`2` por defecto): máximo de rondas de revisión automática por borrador.
   - `STYLE_HEDGING_THRESHOLD` (`1` por defecto): si detecta expresiones de duda (e.g., seems/maybe/might), fuerza revisión.
   - `STYLE_JARGON_BLOCK_THRESHOLD` (`1` por defecto): si detecta jerga corporativa local (banlist), fuerza revisión.
   - `STYLE_AUDIT_JARGON_SCORE_MIN` (`2` por defecto): si el auditor LLM puntúa jerga ≥ valor, fuerza revisión.
   - `STYLE_AUDIT_CLICHE_SCORE_MIN` (`2` por defecto): si el auditor LLM puntúa clichés ≥ valor, fuerza revisión.
 - Variante C (categorías):
   - Las categorías para la variante C se cargan desde `config/post_categories.json` por defecto (puedes sobreescribir con `POST_CATEGORIES_PATH`).
   - Formato por categoría: `{ "key": "...", "name": "Nombre en español", "pattern": "Definición/patrón en inglés", "structure": "Análisis de la estructura (EN)", "why": "Descripción de la técnica (EN)" }`.
   - El bot muestra el `name` en el mensaje ("Categoría (C): …"). El `pattern` guía al LLM (salida siempre en inglés).
- Watchers (estilo):
  - `WATCHER_ENFORCE_STYLE_AUDIT` (`1` por defecto): activa auditoría de estilo al ingerir. Pon `0` para no filtrar por estilo.
  - `WATCHER_JARGON_THRESHOLD` (`4` por defecto) y `WATCHER_CLICHE_THRESHOLD` (`4` por defecto): umbrales para rechazar solo si la voz es `boardroom` y alguno supera el umbral.
 - Validación de temas:
   - `WATCHER_LENIENT_VALIDATION` (`1` por defecto): aprueba salvo que el tema sea claramente ajeno al ámbito COO (operaciones, liderazgo, personas, procesos, sistemas, ejecución, productividad, org design, finanzas ops, product ops, portfolio/roadmap, growth). Pon `0` para validación estricta.
 - Persistencia (ruta ChromaDB):
   - `CHROMA_DB_PATH` (por defecto `db`): ruta de almacenamiento de ChromaDB. En despliegues con Cloud Run, monta un bucket GCS y usa `/mnt/db` como ruta.

Nota: `/.env` está en `.gitignore`. No subas tus claves.

**Instalación Rápida**
- Crear entorno e instalar deps:
  - `python -m venv venv && source venv/bin/activate`
  - `pip install -r requirements.txt`
- Crear `.env` en la raíz del proyecto con las variables anteriores.
  - También puedes partir de `.env.example`.

**Flujos Principales**
- Extraer temas desde PDFs con validación y embeddings:
  - `python watcher_with_metadata.py`
  - Copia PDFs a `uploads/`. El watcher:
    - Extrae texto (PyMuPDF), trocea en chunks y pide 8–12 temas por chunk al LLM.
    - Valida cada tema como “relevante para COO” devolviendo JSON.
    - Opcional: auditoría de estilo para filtrar abstracts demasiado “boardroom” (activado por defecto; desactivar con `WATCHER_ENFORCE_STYLE_AUDIT=0`).
    - Genera embeddings con Google y añade a `topics_collection` en `db/`.
    - Guarda un resumen en `json/<nombre>.json` y muestra notificación de escritorio.

- Generar dos tweets desde un tema (LLM):
  - Vía REPL de Python:
    - `python -i core_generator.py`
    - `generate_tweet_from_topic("<abstract del tema>")`
  - Comportamiento: crea `[EN - A]` y `[EN - B]`, refina estilo y recorta si >280. Durante generación y refinado se carga y se inyecta el contrato `copywriter_contract.md` en los mensajes del LLM para garantizar el cumplimiento del tono y formato. Además, se ejecuta una auditoría de estilo (LLM + heurísticos) con posible reescritura si el tono queda demasiado "boardroom" o genérico.

- Generar dos variantes offline (sin LLM):
  - `python offline_generate.py`
  - Lee un tema aleatorio de `topics_collection` y produce dos alternativas.

**Fallback LLM (OpenRouter → Gemini)**
- Configuración y uso: `llm_fallback.py:1`.
- Orden por defecto (ajustable en `.env`): `FALLBACK_PROVIDER_ORDER=gemini,openrouter`.
- Modelos:
  - OpenRouter: usa tus modelos actuales (p. ej., `anthropic/claude-3.5-sonnet`, `anthropic/claude-3-haiku`).
  - Gemini: por defecto `gemini-2.5-pro`. Si el modelo no está disponible, se prueban alternativas (`gemini-2.5-flash`, `gemini-2.0-flash`, etc.).
- Detección de fallos que activan fallback: errores de crédito (402), rate limit (429), 401/403, y mensajes típicos de “insufficient/quota/credit/billing”.
- Salida JSON robusta: con OpenRouter se usa `response_format={"type":"json_object"}`; con Gemini se fuerza “ONLY strict JSON” y se parsea buscando el primer bloque JSON válido.

Referencias en código:
- `core_generator.py:23` `refine_and_shorten_tweet` → usa `llm.chat_text(...)`.
- `core_generator.py:37` `refine_single_tweet_style` → usa `llm.chat_text(...)`.
- `core_generator.py:104` generación de A/B → usa `llm.chat_text(...)`.
- `watcher_with_metadata.py` → validación `validate_topic` y extracción de temas usan `llm.chat_json(...)`.

**Detalles de Almacenamiento**
- Vector DB: ChromaDB persistente en `db/`. Se crean dos colecciones:
  - `topics_collection`: temas validados (document + embedding + opcional metadata).
  - `memory_collection`: memoria para evitar duplicidad de tweets (`core_generator.py`).
- JSON de salida por PDF en `json/`.

**Logging y Notificaciones**
- Logger central: `logger_config.py:1` (nivel INFO por defecto). Todos los intentos de proveedor y fallbacks se registran.
- Notificaciones de escritorio: `watcher*.py` usa `desktop-notifier` para avisos al finalizar.
 - Mensajería Telegram: los mensajes de propuesta se envían en MarkdownV2 con escape seguro; incluyen Tema, Origen (si existe) y A/B con conteo `(N/280)`. El ID se oculta por defecto salvo que `SHOW_TOPIC_ID=1` o falte `Origen`.

**Solución de Problemas**
- OpenRouter sin créditos:
  - Se activa el fallback a Gemini automáticamente (ver logs).
  - Para priorizar siempre Gemini: `FALLBACK_PROVIDER_ORDER=gemini,openrouter` en `.env`.
- Gemini “model not found/unsupported”:
  - Ajusta `GEMINI_MODEL` a un modelo listado por tu clave. Puedes listar modelos:
    - Python: `import google.generativeai as genai, os; genai.configure(api_key=os.getenv('GOOGLE_API_KEY')); print([m.name for m in genai.list_models()])`
- Embeddings fallan:
  - Revisa `GOOGLE_API_KEY` y permisos para `models/embedding-001`.
- No se añaden temas a ChromaDB:
  - Verifica que haya embeddings válidos y que `db/` sea accesible.

**Ejecución Segura**
- No subas `.env`. Revoca claves si se exponen fuera de tu entorno.
- Revisa `requirements.txt` antes de desplegar a producción.

**Comandos Útiles**
- Instalar deps: `pip install -r requirements.txt`
- Ver modelos Gemini disponibles (Python): ver bloque en “Solución de Problemas”.
- Ejecutar watcher (con metadatos): `python watcher_with_metadata.py`
- Generación offline de dos alternativas: `python offline_generate.py`
- Resetear la memoria (dataset aprobado) de ChromaDB: `python reset_memory.py` (añade `-y` para omitir confirmación)
 - Consultar stats vía HTTP (si se despliega el bot): `GET /stats?token=<ADMIN_API_TOKEN>` devuelve `{topics, memory}`.

**Despliegue recomendado (Cloud Run + GCS)**
- Imagen: usar el `Dockerfile` incluido; comando de arranque `gunicorn -b :8080 bot:app`.
- Montar bucket con GCS FUSE en `/mnt/db` y configurar `CHROMA_DB_PATH=/mnt/db`.
- Variables: `TELEGRAM_BOT_TOKEN`, `GOOGLE_API_KEY`, opcional `OPENROUTER_API_KEY`, `FALLBACK_PROVIDER_ORDER=gemini,openrouter`, `GEMINI_MODEL=gemini-2.5-pro`, `ADMIN_API_TOKEN`, `SHOW_TOPIC_ID=0`.
- Escalado: `--min-instances 0 --max-instances 1` para evitar problemas de concurrencia de SQLite.

**Buenas Prácticas**
- Mantén `.env` fuera del control de versiones y rota claves si se exponen.
- Usa `watcher_with_metadata.py` para enriquecer con metadatos y evitar duplicados al ingerir varios PDFs.
- Ajusta `SIMILARITY_THRESHOLD` en `core_generator.py` si detectas demasiados/escasos “duplicados”.
- Si OpenRouter no tiene créditos, prioriza temporalmente Gemini (`FALLBACK_PROVIDER_ORDER=gemini,openrouter`).
- Para JSON estrictos, mantén indicaciones “Respond ONLY with strict JSON” en prompts y revisa logs si falla el parseo.
- Actualiza `google-generativeai` periódicamente para acceder a modelos más nuevos (`gemini-2.5-*`).

**Contribución**
- Lee primero `AGENTS.md` para entender invariantes, flujo, políticas de LLM/Telegram/Chroma y el runbook operativo.
- Usa commits pequeños (Conventional Commits) y propone antes de cambiar contratos (formato `[EN - A]/[EN - B]`, límite 280, etc.).

**CI/CD (Cloud Build)**
- Trigger: push a `main` despliega automáticamente a Cloud Run.
- Config del pipeline: `deploy/cloudbuild.yaml` (usa volumen Cloud Storage, secretos de Secret Manager y env vars con coma escapada).
- Trigger actual: nombre `activadorx`, región `europe-west1`.
- Ejecutar build manual:
  - `gcloud builds triggers run activadorx --project=xbot-473616 --region=europe-west1 --branch=main`
- Ver estado del servicio y estadísticas:
  - `gcloud run services describe x-bot-mei --region=europe-west1 --format='value(status.url)'`
  - `curl "https://<service-url>/stats?token=<ADMIN_API_TOKEN>"`
