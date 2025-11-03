# AGENTS.md — Guía para agentes y desarrolladores

Este documento instruye a cualquier agente/colaborador sobre cómo trabajar en este repositorio sin romper nada, aportando de forma segura, rápida y con criterios de ingeniería sólidos.

Su alcance aplica a TODO el árbol bajo esta carpeta.

## Principios No Negociables

- No destruyas datos ni reescribas historial sin confirmación explícita.
- Cambios pequeños, enfocados y reversibles; propone antes que “refactorizar por deporte”.
- Respeta las invariantes de negocio:
  - Tweets deben ser ≤ 280 caracteres sin recorte local: solo mediante LLM.
  - Mensajes de Telegram incluyen: tema (abstract), nombre del PDF origen si existe, y contadores `(N/280)` por opción.
- Los temas en ChromaDB incluyen metadatos `{"pdf": <nombre>}` cuando provienen del pipeline de ingestión (`watcher_app.py` + módulos auxiliares).
- LLM: uso exclusivo de OpenRouter. No hay fallback a otros proveedores.
- Preferir seguridad y claridad sobre “optimización prematura”.

## Arquitectura (vista de 10.000 ft)

- `watcher_app.py`
  - Observa `uploads/` para nuevos PDFs y delega en módulos especializados.
- `ingestion_config.py`
  - Carga configuración desde `.env` y garantiza directorios de trabajo.
- `pdf_extractor.py`
  - Convierte cada PDF a texto plano usando PyMuPDF.
- `topic_pipeline.py`
  - Gestiona extracción de tópicos (LlamaIndex + fallback), validación de relevancia COO y gating de estilo.
- `persistence_service.py`
  - Inserta tópicos en Chroma, sincroniza con endpoints remotos y genera resúmenes JSON.
- `embeddings_manager.py`
  - Cliente único de ChromaDB (persistente) con lock de inicialización; funciones para colecciones y embeddings vía OpenRouter (modelo por defecto `openai/text-embedding-3-small`).
- `core_generator.py`
  - Selecciona un tema de `topics_collection`, comprueba similitud contra `memory_collection` y genera A/B bajo contrato de estilo.
  - Refinado de estilo + “acortado iterativo” vía LLM hasta ≤ 280 (sin truncar localmente).
- `variant_generators.py`
  - Encapsula prompts, refinamientos, control de longitud y selección de categorías para las variantes A/B/C.
  - Antes de escribir cada borrador, genera ángulos “tail” (probabilidad < 0.15) para romper narrativas mainstream y pegarlas como columna vertebral de los textos.
  - Después de generar cada variante, corre un debate interno (contrarian, compliance, clarity reviewers) y reescribe con ese feedback antes del pulido final.
  - Usa `writing_rules.py` para rotar formatos (stair up/down, staccato, list strikes), asignar hooks (dolor, vulnerabilidad, controversia, credibilidad, curiosidad, cambio radical) y bloquear vocabulario o sintaxis prohibida (sin comas, sin “-mente”/“-ly”, sin “y/o”).
  - Cada variante valida el formato y la limpieza léxica antes de entregarse; si no respeta la escalera/ritmo asignado o usa palabras vetadas, se rechaza y se regenera.
- `prompt_context.py`
  - Provee el bundle del contrato, ICP y pautas de revisión para inyectar en los prompts.
- `writing_rules.py`
  - Fuente única de formatos, hooks, bloqueos de vocabulario y validadores de estructura usados por los generadores y `/comentar`.
- `evaluation.py`
  - Evalúa cada borrador con rubricas de estilo, señal contraria, claridad y factualidad (formato G-Eval) y devuelve un resumen que se adjunta en Telegram.
- `telegram_client.py`
  - Cliente HTTP + helpers de formato (HTML seguro, teclados) para Telegram.
- `proposal_service.py`
  - Orquesta selección de temas, generación A/B/C y manejo de callbacks del bot.
- `admin_service.py`
  - Lógica de ingestión remota y estadísticas (`/pdfs`, `/stats`).
- `draft_repository.py`
  - Guarda y recupera los borradores por chat/tema de forma atómica.
- `callback_parser.py`
  - Tipifica y parsea los datos de los botones inline de Telegram.
- `llm_fallback.py`
  - Capa común de LLM: OpenRouter con manejo de JSON robusto y detección de errores (sin fallback a otros proveedores).
- `bot.py`
  - Webhook de Telegram: `/generate` para proponer A/B y callbacks para aprobar/rechazar.
  - Envía mensajes con comprobación de errores Telegram (Markdown → plano si falla).
  - Gestiona archivos temporales en `/tmp` para conservar borradores entre callbacks.
- `copywriter_contract_hormozi.md`
  - Contrato creativo/estilístico activo. `copywriter_contract.md` queda como referencia histórica y puede reactivarse vía `STYLE_CONTRACT_PATH`.
- `config/final_review_guidelines.md`
  - Pautas complementarias anti-cliché/anti-IA aplicadas en la revisión final (modo "warden"): si no se cumplen, la generación se rechaza con feedback. Puedes cambiar el archivo con `FINAL_REVIEW_GUIDELINES_PATH`.
- `notion_ops.py` / `notifications.py`
  - Utilidades compartidas para consultar Notion, marcar páginas y enviar alertas a Telegram.
- `scripts/notion_report.py`, `scripts/promote_and_notify.py`, `scripts/promote_daily.sh`
  - Scripts CLI para el scheduler (GitHub Actions o cron) que avisan de pendientes y promueven automáticamente.
- `ProposalService`
  - Si `SHOW_INTERNAL_SUMMARY=1`, envía en Telegram un resumen del tail sampling y los principales comentarios del debate interno después de cada propuesta.
  - Con `/comentar <texto>` evalúa primero si el post merece respuesta para el ICP; si pasa el filtro, genera un único comentario (contrato + auditor externo) y lo entrega en bloque `<code>` listo para copiar.
- `reddit_ingestion.py` *(pausado)*  
  - Debe habilitarse con `ENABLE_REDDIT_INGESTION=1` antes de ejecutarse. Recupera posts recientes vía JSON API pública, aplica filtros por keywords (`config/reddit_sources.json`) y pasa el evaluador/tópicos del radar.
- `huggingface_ingestion/` + `hf_ingestion.py`
  - Procesan señales curadas/manuales desde Hugging Face (config `config/hf_sources.json`), aplican evaluador “cabrón”, generan candidatos con metadatos y los guardan en JSON + índice compartido.
- `hf_notion_sync.py` / `promote_notion_topics.py`
  - Sincronizan candidatos con Notion para revisión humana y promueven los marcados como “Validated” a ChromaDB (`status=approved`).

## Configuración y Entorno

- Python 3.10+.
- Instalación (runtime): `python -m venv venv && source venv/bin/activate && pip install -r requirements.runtime.txt`.
- Extras de desarrollo: `pip install -r requirements.dev.txt`.
- Flujos programados/CI: `pip install -r requirements.workflow.txt`.
- Variables de entorno (.env, no subir nunca):
  - `OPENROUTER_API_KEY` (obligatoria)
  - `FALLBACK_PROVIDER_ORDER` (por defecto `openrouter`)
  - `GENERATION_MODEL` (por defecto `x-ai/grok-4`)
  - `VALIDATION_MODEL` (por defecto `x-ai/grok-4-fast`)
  - `EMBED_MODEL` (por defecto `openai/text-embedding-3-small`)
  - `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (si usas el bot)
  - `NOTION_API_TOKEN`, `NOTION_DATABASE_ID` (si sincronizas curación con Notion)
  - `HF_SOURCES_PATH`, `HF_CANDIDATE_DIR`, `HF_CANDIDATE_INDEX`, `HF_STATE_PATH` (override opcional de rutas del pipeline Hugging Face)
  - Overrides opcionales: `STYLE_CONTRACT_PATH`, `ICP_PATH`, `FINAL_REVIEW_GUIDELINES_PATH`

## Flujo de Datos (camino feliz)

0) Radar (automático): `hf_ingestion.py` crea candidatos (`status=candidate`) → `hf_notion_sync.py` los sube a Notion → humano marca `Validated` → `promote_and_notify.py` actualiza `status=approved`, avisa por Telegram y los añade a `topics_collection`.
1) Ingesta tradicional: PDF a `uploads/` → texto → chunks → extracción temas (JSON) → validación COO (JSON) → embeddings → `topics_collection`.
2) Generación: `core_generator.generate_tweet_from_topic(abstract)` produce `[EN - A]` y `[EN - B]` ≤ 280 (iterativo LLM), revisa similitud previa.
3) Bot: `/generate` → elige tema aprobado o fallback general, muestra A/B con tema+PDF+conteo, callbacks para aprobar y persistir en `memory_collection`.

## Políticas de LLM (Actualizado Octubre 2025)

- **Principio de Inferencia Única:** Se debe priorizar la consolidación de múltiples pasos de razonamiento (ej. `tail-sampling`, `debate interno`, refinado) en una única llamada al LLM con un prompt de "Chain of Thought" bien estructurado. El objetivo es mover la complejidad del *proceso* (múltiples llamadas secuenciales) a la *instrucción* (un prompt más inteligente).
- **Evaluación Adaptativa:** Para tareas de auditoría, se debe usar un sistema de dos jueces. Un modelo rápido y barato (`flash`) para criterios objetivos y cuantificables, y un modelo potente (`pro`) solo si es necesario para el juicio subjetivo, basándose en un umbral de confianza.
- **Configuración Externalizada:** Las rúbricas y prompts complejos deben vivir en ficheros de configuración (`.yaml`) para facilitar su modificación sin tocar el código de la aplicación.
- **Orden de proveedores:** `FALLBACK_PROVIDER_ORDER` (por defecto `openrouter`).
- **JSON estricto:** Forzar siempre la salida en formato JSON para facilitar el parseo y la fiabilidad.

## Telegram: Mensajería y Callbacks

- Mensaje de propuesta debe incluir:
  - “Tema” (abstract) y “Origen” (nombre de PDF) si están disponibles.
  - Dos opciones con contador `(N/280)`.
  - ID del tema opcional en encabezado: oculto por defecto para limpieza visual; se muestra si `SHOW_TOPIC_ID=1` o si no hay `Origen` (para trazabilidad).
- Envío robusto:
  - Se usa `parse_mode=MarkdownV2` con escape seguro; fallback a `Markdown` y luego a texto plano si fuese necesario.
  - Validar respuesta `ok` y `status_code`; loggear `description` en caso de error.
- Callbacks:
  - Formato: `approve_A_<topic_id>`, `approve_B_<topic_id>`, `reject_<topic_id>`, `generate_new`.
  - Parsear con `split('_', 2)` para no corromper el `topic_id`.
- Archivos temporales en `/tmp`:
  - Nombre: `/{chat_id}_{topic_id}.tmp` (JSON con `draft_a` y `draft_b`).
  - Comprobar existencia antes de leer; si falta, avisar al usuario y ofrecer botón para regenerar.

## ChromaDB: Persistencia y Colecciones

- Cliente único con `threading.Lock()` para inicializar; evita error de tenant/bindings por carrera.
- Rutas y colecciones:
  - `db/` persistente (no borrar salvo “fresh start” explícito).
  - `topics_collection` (embeddings de temas, `documents=[abstract]`, `metadatas=[{"pdf": name}] si aplica`).
  - `memory_collection` (tweets aprobados para evitar duplicados; embedding del texto final aprobado).
- IDs de temas: hashes cortos (p.ej., MD5[:10]). Deben permanecer estables por `abstract` para deduplicar.

## Contratos de Interfaz (invariantes)

- `generate_tweet_from_topic(abstract)`:
  - Entrada: `abstract: str`.
  - Salida: `(draft_a: str, draft_b: str)` ambos ≤ 280; si algo falla, `("Error: ...", "")`.
- `find_relevant_topic()` devuelve `{"topic_id", "abstract", "source_pdf"?}` o `None`.
- Watchers escriben JSON de salida en `json/<pdf>.json` y añaden a Chroma.

## Estilo de Código y Commits

- Python claro, sin “trucos”; nombres descriptivos; evita variables de una letra.
- No añadas cabeceras de licencia nuevas.
- Commits atómicos, mensajes tipo Conventional Commits (`feat:`, `fix:`, `docs:`...).
- No cambies nombres/ubicaciones públicas salvo necesidad justificada.

## Patrones de Error y Diagnóstico

- OpenRouter: si hay errores 401/403/402/429, se registra el error y se aborta. No hay fallback a otros proveedores.
- Telegram 400 “can’t parse entities” → ocurre por Markdown; ya hay fallback a texto plano. Considera escape MarkdownV2 si se requiere formato estable.
- Chroma “Could not connect to tenant …” → carrera en init; ya mitigado con lock. Si persiste, verifica permisos y la existencia de `db/`.

## Pruebas y Validación (rápido y seguro)

- Smoke tests sin red:
  - Monkeypatch `llm._openrouter_chat` para probar manejo de errores y robustez de parseo JSON.
- Watcher local:
  - Ejecuta `python watcher_app.py`, copia un PDF pequeño a `uploads/` y verifica `json/` + `db/` + notificación.
- Bot:
  - Con `/generate` debe llegar propuesta con tema+PDF y conteos. Aprobación A/B genera un enlace listo para publicar en Threads y guarda en memoria.

## Operación y Runbook

- Fresh start de la BD: borrar `db/` y recrear (vacía). Reiniciar watchers/bot.
- Rotar claves: actualiza `.env` y reinicia procesos.
- Priorizar proveedor: ajusta `FALLBACK_PROVIDER_ORDER` y reinicia.
- Registrar incidencias: capturar logs (INFO/WARNING/ERROR) con timestamps.

Acceso y despliegue (referencia)
- Para detalles operativos (acceso GitHub/GCP, CI/CD, secretos y endpoints), consulta `ACCESS_CLI.md`:1

## Mejores Prácticas (aplícalas siempre)

- Antes de cambiar código:
  - Lee este AGENTS.md y el README.
  - Levanta un plan por pasos (qué, dónde, por qué). Consulta al usuario en cambios sensibles.
- Al tocar prompts:
  - Mantén instrucciones claras (JSON-only cuando se espera), prohíbe comillas si luego molestan, pide concisión antes que densidad.
- Al tocar el bot:
  - Mantén el parseo robusto de callbacks y el manejo de fallos de Telegram. No confíes en que 200 significa “entregado”.
- Al tocar watchers:
  - No bloquees el hilo con operaciones largas; respeta el patrón actual. Mantén `chunk_size` y `overlap` razonables.
- Al persistir en ChromaDB:
  - Asegura metadatos del PDF cuando existan. No cambies nombres de colecciones sin migración.

## Propuestas OTB (alternativas “Out‑of‑the‑Box”)

### Ruta recomendada (impacto alto → bajo)
1. **Self-Consistency / Debate interno**: añadir revisores automáticos (contrarian, compliance, factual) que discutan la respuesta antes del output final.
2. **Chain of Contrast**: forzar al modelo a comparar narrativas opuestas y elegir la que golpea más fuerte (complementa el tail sampling actual).
3. **Evaluadores SOTA**: usar evaluadores externos (G-Eval2, Claude Sonnet, Llama Guard) para revisión de estilo/factualidad con mayor precisión.
4. **Retrieval contextual**: ingerir señales frescas (Reddit, Discord, newsletters) con embeddings y jobs automáticos; los temas se nutren de datos vivos.
5. **ReAct / Toolformer**: permitir que el LLM invoque herramientas (búsqueda, cálculos rápidos) para añadir cifras o hechos relevantes.
6. **Feedback loop con métricas reales**: usar CTR/replies para priorizar ángulos, descartar duplicados y retroalimentar el tail sampling.
7. **Persona tuning ligero (LoRA/instruct)**: entrenar un adaptador con los mejores posts para fijar voz/ritmo sin reescribir el contrato principal.

### Ideas adicionales
- Circuit breaker de proveedor: cachear fallos de OpenRouter y evitar reintentos inmediatos.
- Escape MarkdownV2 opcional: helper para evitar errores 400 sin perder formato.
- Scheduler local (cron/apscheduler) para generación programada fuera de GitHub Actions.
- Métricas mínimas: contador de entregas, ratio de fallback, ratio ≤280, tiempos medios (logging agregable).
- Límite objetivo de 260 caracteres en la primera pasada para aumentar probabilidades de quedar ≤280 tras refinado.
- Cola local (disk-backed) para propuestas en caso de red inestable.

## Qué NO hacer sin aprobación explícita

- Cambiar el contrato creativo (`copywriter_contract_hormozi.md` o el definido por `STYLE_CONTRACT_PATH`) o el formato `[EN - A] / [EN - B]`.
- Alterar las pautas de revisión final (`config/final_review_guidelines.md` o `FINAL_REVIEW_GUIDELINES_PATH`) sin alinear con el contrato vigente.
- Reemplazar la capa `llm_fallback.py` por llamadas directas.
- Quitar los contadores `(N/280)` en Telegram.
- Modificar umbrales de similitud o colecciones de Chroma sin revisar impacto.

## Checklist antes de mergear un cambio

- [ ] Plan claro y cambio acotado.
- [ ] Logs informativos añadidos/ajustados.
- [ ] Sin truncado local en tweets; pruebas con textos largos.
- [ ] Envío a Telegram validado (ok=true o fallback a texto plano).
- [ ] Mantener metadatos `pdf` en temas nuevos.
- [ ] Probado con `FALLBACK_PROVIDER_ORDER` actual.
- [ ] Documentado en README si afecta a uso.
