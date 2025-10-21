**Guía Rápida (4 pasos)**
- 1) Instalar: `python -m venv venv && source venv/bin/activate && pip install -r requirements.txt`.
- 2) Configurar `.env`: define `GOOGLE_API_KEY`, opcional `OPENROUTER_API_KEY`, y deja `FALLBACK_PROVIDER_ORDER=gemini,openrouter`, `GEMINI_MODEL=gemini-2.5-pro`.
- 3) Iniciar watcher local: `python run_watcher.py` y copia un PDF a `uploads/`. Se extraen, validan y guardan temas en `db/`.
- 4) Generar tweets: `python -i core_generator.py` y ejecuta `generate_tweet_from_topic("<abstract>")`.
- Nota rápida: si tu sistema usa `python3`, ajusta los comandos anteriores a `python3` y `python3 -m venv`.

**Resumen**
- Genera ideas de tweets a partir de PDFs, valida su relevancia para un perfil COO y almacena los temas en una base vectorial (ChromaDB).
- A partir de un tema, crea dos alternativas de tweet refinadas y bajo 280 caracteres.
- Usa una capa LLM con fallback automático: intenta OpenRouter y, si no hay créditos o hay error, continúa con Gemini.

**Componentes**
- `llm_fallback.py`: capa común para LLM con orden de proveedores y manejo de JSON.
- `embeddings_manager.py`: embeddings con Google Generative AI (`models/embedding-001`) y cliente persistente de ChromaDB.
- `ingestion_config.py`: carga configuración del watcher y prepara directorios.
- `pdf_extractor.py`: utilidades simples para convertir PDFs en texto plano.
- `topic_pipeline.py`: extracción de tópicos (LlamaIndex + fallback), validación y gating de estilo.
- `persistence_service.py`: persiste tópicos en Chroma, sincroniza con endpoints remotos y genera resúmenes JSON.
- `watcher_v2.py`: lógica del watcher que observa `uploads/` y delega en los módulos anteriores.
- `run_watcher.py`: punto de entrada para ejecutar el watcher local.
- `huggingface_ingestion/`: adapta datasets del Hub (config `config/hf_sources.json`), ejecuta un evaluador estricto y genera candidatos con metadatos listos para revisión.
- `hf_ingestion.py`: CLI para descargar señales gratuitas (Hugging Face), evaluarlas y guardar candidatos en `json/hf_candidates/` + índice en `db/hf_candidate_records.json`.
- `hf_notion_sync.py`: sube/actualiza los candidatos en una base Notion para revisión humana.
- `promote_notion_topics.py`: toma los ítems validados en Notion, los marca como `approved` y los persiste en Chroma priorizados por el generador.
- `core_generator.py`: orquesta la generación de A/B/C, controla similitud y reintentos.
- `variant_generators.py`: encapsula prompts, refinamientos y selección de categorías para cada variante.
- `prompt_context.py`: agrupa contrato, ICP y pautas complementarias para inyectarlos en los prompts.
- `evaluation.py`: auditoría automática (tono/factualidad) que acompaña cada borrador sugerido.
- `telegram_client.py`: cliente HTTP + helpers de formato/teclados para Telegram.
- `proposal_service.py`: servicio que coordina selección de temas, generación A/B/C y callbacks.
- `admin_service.py`: utilidades de ingestión y estadísticas (`/stats`, `/pdfs`, `/ingest_topics`).
- `draft_repository.py`: persistencia temporal de borradores aprobables/copiables por chat.
- `callback_parser.py`: tipifica y parsea las acciones de los botones inline.
- `offline_generate.py`: genera 2 variantes de tweet sin LLM a partir de un tema aleatorio (útil para pruebas rápidas).
- `bot.py`: app Flask que enruta comandos/ callbacks y delega en los servicios anteriores.
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
  - `NOTION_API_TOKEN`, `NOTION_DATABASE_ID` si conectas la revisión humana vía Notion.
  - `HF_SOURCES_PATH`, `HF_CANDIDATE_DIR`, `HF_CANDIDATE_INDEX`, `HF_STATE_PATH` para ajustar rutas del pipeline Hugging Face.
  - Añade `GOOGLE_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` y, si usas GitHub Actions, la variable `HF_INGEST_LIMIT` en los Secrets/Variables del repositorio.
  - `TAIL_SAMPLING_COUNT` (opcional): número de ángulos contrarios generados antes de cada borrador (por defecto 3).
  - `SHOW_INTERNAL_SUMMARY` (`0` por defecto): si `1`, envía en Telegram el resumen del debate interno/tail sampling tras la propuesta.
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
- Voz y persona (opcional):
  - `STYLE_CONTRACT_PATH`: ruta alternativa al contrato creativo (por defecto `copywriter_contract_hormozi.md`). Permite apuntar a otra versión del tono sin tocar el repo.
  - `ICP_PATH`: ruta alternativa al archivo del ICP (por defecto `config/icp.md`). Útil para testear diferentes públicos meta.
  - `FINAL_REVIEW_GUIDELINES_PATH`: ruta alternativa a las pautas de revisión complementarias (por defecto `config/final_review_guidelines.md`). Ajusta las reglas anti-cliché/anti-IA sin tocar el contrato principal.
- Publicación:
  - `THREADS_SHARE_URL` (opcional, por defecto `https://www.threads.net/intent/post?text=`) ajusta el enlace que se abre al aprobar una opción.
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
- **Local PDF Ingestion Watcher:**
  - Ejecuta el watcher en tu máquina local para procesar PDFs y alimentar la base de datos de temas.
  - Comando: `python run_watcher.py`
  - Simplemente copia los archivos PDF que quieras procesar en el directorio `uploads/`.
  - El watcher detectará los nuevos archivos, extraerá el texto, generará temas y embeddings, y los guardará en la base de datos ChromaDB.

- Generar dos tweets desde un tema (LLM):
  - Vía REPL de Python:
    - `python -i core_generator.py`
    - `generate_tweet_from_topic("<abstract del tema>")`
  - Comportamiento: crea `[EN - A]` y `[EN - B]`, refina estilo y recorta si >280. Antes de escribir, el modelo genera ángulos “tail” (p < 0.15) que desafían la narrativa mainstream y sirven de columna vertebral para cada variante. Luego pasa por un debate interno con tres revisores (contrarian, compliance y clarity) que devuelven feedback; el borrador se reescribe incorporando esas notas antes del pulido final. Durante generación y refinado se inyectan el contrato configurado (por defecto `copywriter_contract_hormozi.md`), el ICP (`config/icp.md`) y las pautas de revisión complementarias (`config/final_review_guidelines.md`) para asegurar voz, audiencia y naturalidad humana. Antes de enviar, cada borrador recibe una evaluación automática (tono/factualidad) que se muestra al usuario; si el revisor final detecta desviaciones graves, la generación se rechaza con feedback explícito. Cada variante llega en bloque `<code>` para copiarla fácilmente, y al aprobar usas el botón “Publicar” que abre Threads con el texto listo.
- Responder publicaciones externas (modo comentario):
  - En el bot de Telegram envía `/c <texto del post>`. Primero evalúa si vale la pena participar (solo responde si aporta valor real para el ICP); luego pasa por el contrato/ICP, el validador externo y devuelve un único comentario en bloque `<code>`, cerrado con una pregunta para seguir la conversación.

- Generar dos variantes offline (sin LLM):
  - `python offline_generate.py`
  - Lee un tema aleatorio de `topics_collection` y produce dos alternativas.
- Radar Reddit/Hugging Face → Notion → Aprobado:
  - `python reddit_ingestion.py` *(pausado)*: define `ENABLE_REDDIT_INGESTION=1` antes de ejecutarlo si quieres reactivar la ingesta experimental desde `config/reddit_sources.json`.
  - `python hf_ingestion.py` procesa señales curadas/manuales desde Hugging Face (`config/hf_sources.json`).
  - `python hf_notion_sync.py` sube/actualiza los candidatos en tu base Notion para revisión.
  - Marca en Notion los ítems que superan las preguntas como `Validated`.
  - `python scripts/promote_and_notify.py` promueve los validados, marca `status=approved`, los añade a ChromaDB y avisa por Telegram si hubo novedades.
  - Opcional: si `SHOW_INTERNAL_SUMMARY=1`, tras cada propuesta en Telegram verás el resumen del razonamiento interno (tail angles, contraste ganador y el primer comentario de cada revisor A/B/C).
  - Cada borrador también pasa por un evaluador estilo G-Eval: puntúa estilo, señal contraria, claridad y factualidad; en Telegram verás ⭐ estilo, factualidad y una nota destacada.
  - Base Notion sugerida: propiedades `Name` (Title), `Status` (Select: Review/Validated/Promoted), `Candidate ID`, `Topic ID`, `Pain`, `Leverage`, `Stage` (Select), `Tags` (Multi-select), `Snippet`, `Source`, `Dataset`, `Source Fields`, `ICP Fit`, `Actionable`, `Stage Context`, `Urgency`, `Synced` (Checkbox).
  - Automatiza la promoción diaria:
    - **Local cron (opcional):** exporta `NOTION_API_TOKEN` y `NOTION_DATABASE_ID`, luego añade a `crontab -e` algo como `0 7 * * * /home/mei/Desktop/MMEI/x_bot_mei/scripts/promote_daily.sh`. El script registra los resultados en `logs/promote.log`.
    - **GitHub Actions:** `.github/workflows/promote.yml` automatiza ingestión → sync → recordatorio → promoción a las 07:00 UTC (también via *workflow_dispatch*). Configura los secretos `NOTION_API_TOKEN`, `NOTION_DATABASE_ID`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `GOOGLE_API_KEY` y la variable `HF_INGEST_LIMIT` si quieres ajustar el límite de ingestión.

**Fallback LLM (OpenRouter → Gemini)**
- Configuración y uso: `llm_fallback.py:1`.
- Orden por defecto (ajustable en `.env`): `FALLBACK_PROVIDER_ORDER=gemini,openrouter`.
- Modelos:
  - OpenRouter: usa tus modelos actuales (p. ej., `anthropic/claude-3.5-sonnet`, `anthropic/claude-3-haiku`).
- Gemini: por defecto `gemini-2.5-pro`. Si ese modelo no está disponible en tu cuenta, ajusta `GEMINI_MODEL` a uno listado.
- Detección de fallos que activan fallback: errores de crédito (402), rate limit (429), 401/403, y mensajes típicos de “insufficient/quota/credit/billing”.
- Salida JSON robusta: con OpenRouter se usa `response_format={"type":"json_object"}`; con Gemini se fuerza “ONLY strict JSON” y se parsea buscando el primer bloque JSON válido.

Referencias en código:
- `core_generator.py` orquesta la generación y reintentos de A/B/C.
- `variant_generators.py` encapsula los prompts, refinamientos y auditorías de estilo para cada variante.
- `topic_pipeline.py` combina extracción de tópicos (LlamaIndex + fallback) y validación estilo/relevancia.

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
- Ingestar señales y notificar: `python hf_ingestion.py --notify`
- Recordatorio de revisión: `python scripts/notion_report.py --status Review`
- Promover y avisar: `python scripts/promote_and_notify.py`
- Ejecutar watcher (con metadatos): `python watcher_app.py`
- Generación offline de dos alternativas: `python offline_generate.py`
- Resetear la memoria (dataset aprobado) de ChromaDB: `python reset_memory.py` (añade `-y` para omitir confirmación)
 - Consultar stats vía HTTP (si se despliega el bot): `GET /stats?token=<ADMIN_API_TOKEN>` devuelve `{topics, memory}`.
- Ingestar señales Hugging Face + generar candidatos: `python hf_ingestion.py --limit 200` (usa `config/hf_sources.json`; genera JSONL e índice).
- Sincronizar candidatos con Notion: `python hf_notion_sync.py --status Review` (requiere `NOTION_API_TOKEN` y `NOTION_DATABASE_ID`).
- Promover temas validados desde Notion: `python promote_notion_topics.py --status Validated --set-status Promoted`.

**Despliegue recomendado (Cloud Run + GCS)**
- Imagen: usar el `Dockerfile` incluido; comando de arranque `gunicorn -b :8080 bot:app`.
- Montar bucket con GCS FUSE en `/mnt/db` y configurar `CHROMA_DB_PATH=/mnt/db`.
- Variables: `TELEGRAM_BOT_TOKEN`, `GOOGLE_API_KEY`, opcional `OPENROUTER_API_KEY`, `FALLBACK_PROVIDER_ORDER=gemini,openrouter`, `GEMINI_MODEL=gemini-2.5-pro`, `ADMIN_API_TOKEN`, `SHOW_TOPIC_ID=0`.
- Escalado: `--min-instances 0 --max-instances 1` para evitar problemas de concurrencia de SQLite.

**Buenas Prácticas**
- Mantén `.env` fuera del control de versiones y rota claves si se exponen.
- Usa `watcher_app.py` (más los módulos auxiliares) para enriquecer con metadatos y evitar duplicados al ingerir varios PDFs.
- Ajusta `SIMILARITY_THRESHOLD` (env var o en `core_generator.py`) si detectas demasiados/escasos “duplicados” (distancia coseno; menor = menos estricto).
- Si OpenRouter no tiene créditos, prioriza temporalmente Gemini (`FALLBACK_PROVIDER_ORDER=gemini,openrouter`).
- Para JSON estrictos, mantén indicaciones “Respond ONLY with strict JSON” en prompts y revisa logs si falla el parseo.
- Actualiza `google-generativeai` periódicamente para acceder a modelos más nuevos (`gemini-2.5-*`).

**Contribución**
- Lee primero `AGENTS.md` para entender invariantes, flujo, políticas de LLM/Telegram/Chroma y el runbook operativo.
- Usa commits pequeños (Conventional Commits) y propone antes de cambiar contratos (formato `[EN - A]/[EN - B]`, límite 280, etc.).

**Acceso y Operación (CLI)**
- Detalles de acceso, CI/CD, GCP, secretos y endpoints: `ACCESS_CLI.md`:1

**CI/CD (Cloud Build)**
- Trigger: push a `main` despliega automáticamente a Cloud Run.
- Config del pipeline: `deploy/cloudbuild.yaml` (usa volumen Cloud Storage, secretos de Secret Manager y env vars con coma escapada).
- Trigger actual: nombre `activadorx`, región `europe-west1`.
- Ejecutar build manual:
  - `gcloud builds triggers run activadorx --project=xbot-473616 --region=europe-west1 --branch=main`
- Ver estado del servicio y estadísticas:
  - `gcloud run services describe x-bot-mei --region=europe-west1 --format='value(status.url)'`
  - `curl "https://<service-url>/stats?token=<ADMIN_API_TOKEN>"`