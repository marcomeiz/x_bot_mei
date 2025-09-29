**Guía Rápida (4 pasos)**
- 1) Instalar: `python -m venv venv && source venv/bin/activate && pip install -r requirements.txt`.
- 2) Configurar `.env`: define `GOOGLE_API_KEY`, opcional `OPENROUTER_API_KEY`, y deja `FALLBACK_PROVIDER_ORDER=gemini,openrouter`, `GEMINI_MODEL=gemini-2.5-pro`.
- 3) Iniciar watcher: `python watcher.py` y copia un PDF a `uploads/`. Se extraen, validan y guardan temas en `db/` + `json/`.
- 4) Generar tweets: `python -i core_generator.py` y ejecuta `generate_tweet_from_topic("<abstract>")`.

**Resumen**
- Genera ideas de tweets a partir de PDFs, valida su relevancia para un perfil COO y almacena los temas en una base vectorial (ChromaDB).
- A partir de un tema, crea dos alternativas de tweet refinadas y bajo 280 caracteres.
- Usa una capa LLM con fallback automático: intenta OpenRouter y, si no hay créditos o hay error, continúa con Gemini.

**Componentes**
- `llm_fallback.py`: capa común para LLM con orden de proveedores y manejo de JSON.
- `embeddings_manager.py`: embeddings con Google Generative AI (`models/embedding-001`) y cliente persistente de ChromaDB.
- `watcher.py`: observa `uploads/`, extrae texto de PDFs, genera y valida temas, y guarda en `json/` + `topics_collection`.
- `watcher_with_metadata.py`: como `watcher.py`, pero añade metadatos (por ejemplo el nombre del PDF) y filtros de duplicados más estrictos.
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

Nota: `/.env` está en `.gitignore`. No subas tus claves.

**Instalación Rápida**
- Crear entorno e instalar deps:
  - `python -m venv venv && source venv/bin/activate`
  - `pip install -r requirements.txt`
- Crear `.env` en la raíz del proyecto con las variables anteriores.

**Flujos Principales**
- Extraer temas desde PDFs con validación y embeddings:
  - `python watcher.py`  o  `python watcher_with_metadata.py`
  - Copia PDFs a `uploads/`. El watcher:
    - Extrae texto (PyMuPDF), trocea en chunks y pide 8–12 temas por chunk al LLM.
    - Valida cada tema como “relevante para COO” devolviendo JSON.
    - Genera embeddings con Google y añade a `topics_collection` en `db/`.
    - Guarda un resumen en `json/<nombre>.json` y muestra notificación de escritorio.

- Generar dos tweets desde un tema (LLM):
  - Vía REPL de Python:
    - `python -i core_generator.py`
    - `generate_tweet_from_topic("<abstract del tema>")`
  - Comportamiento: crea `[EN - A]` y `[EN - B]`, refina estilo y recorta si >280.

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
- `watcher.py:68` validación `validate_topic` → usa `llm.chat_json(...)`.
- `watcher.py:97` extracción de temas → usa `llm.chat_json(...)`.
- `watcher_with_metadata.py:72` y `:97` → mismos cambios con metadatos y filtros de duplicado.

**Detalles de Almacenamiento**
- Vector DB: ChromaDB persistente en `db/`. Se crean dos colecciones:
  - `topics_collection`: temas validados (document + embedding + opcional metadata).
  - `memory_collection`: memoria para evitar duplicidad de tweets (`core_generator.py`).
- JSON de salida por PDF en `json/`.

**Logging y Notificaciones**
- Logger central: `logger_config.py:1` (nivel INFO por defecto). Todos los intentos de proveedor y fallbacks se registran.
- Notificaciones de escritorio: `watcher*.py` usa `desktop-notifier` para avisos al finalizar.

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
- Ejecutar watcher simple: `python watcher.py`
- Ejecutar watcher con metadatos: `python watcher_with_metadata.py`
- Generación offline de dos alternativas: `python offline_generate.py`

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
