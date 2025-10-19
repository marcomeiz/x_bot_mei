# AGENTS.md — Guía para agentes y desarrolladores

Este documento instruye a cualquier agente/colaborador sobre cómo trabajar en este repositorio sin romper nada, aportando de forma segura, rápida y con criterios de ingeniería sólidos.

Su alcance aplica a TODO el árbol bajo esta carpeta.

## Principios No Negociables

- No destruyas datos ni reescribas historial sin confirmación explícita.
- Cambios pequeños, enfocados y reversibles; propone antes que “refactorizar por deporte”.
- Respeta las invariantes de negocio:
  - Tweets deben ser ≤ 280 caracteres sin recorte local: solo mediante LLM.
  - Mensajes de Telegram incluyen: tema (abstract), nombre del PDF origen si existe, y contadores `(N/280)` por opción.
- Los temas en ChromaDB incluyen metadatos `{"pdf": <nombre>}` cuando provienen de `watcher_with_metadata.py`.
- Fallback LLM: intentar OpenRouter y, si falla (402/401/403/429/insufficient), pasar a Gemini sin interrumpir el flujo.
- Preferir seguridad y claridad sobre “optimización prematura”.

## Arquitectura (vista de 10.000 ft)

- `watcher_with_metadata.py`
  - Observan `uploads/` para nuevos PDFs.
  - Extraen texto (PyMuPDF), trocean, piden 8–12 temas por chunk (JSON), validan relevancia COO, generan embeddings, y persisten en ChromaDB.
  - Guardan un JSON resumen por PDF en `json/`.
- `embeddings_manager.py`
  - Cliente único de ChromaDB (persistente) con lock de inicialización; funciones para colecciones y embeddings (Google `models/embedding-001`).
- `core_generator.py`
  - Selecciona un tema de `topics_collection`, comprueba similitud contra `memory_collection` y genera A/B bajo contrato de estilo.
  - Refinado de estilo + “acortado iterativo” vía LLM hasta ≤ 280 (sin truncar localmente).
- `variant_generators.py`
  - Encapsula prompts, refinamientos, control de longitud y selección de categorías para las variantes A/B/C.
- `prompt_context.py`
  - Provee el bundle del contrato, ICP y pautas de revisión para inyectar en los prompts.
- `telegram_client.py`
  - Cliente HTTP + helpers de formato (HTML seguro, teclados) para Telegram.
- `proposal_service.py`
  - Orquesta selección de temas, generación A/B/C y manejo de callbacks del bot.
- `admin_service.py`
  - Lógica de ingestión remota y estadísticas (`/pdfs`, `/stats`).
- `llm_fallback.py`
  - Capa común de LLM: intenta OpenRouter → Gemini, con manejo de JSON robusto y detección de errores para fallback.
- `bot.py`
  - Webhook de Telegram: `/generate` para proponer A/B y callbacks para aprobar/rechazar.
  - Envía mensajes con comprobación de errores Telegram (Markdown → plano si falla).
  - Gestiona archivos temporales en `/tmp` para conservar borradores entre callbacks.
- `copywriter_contract_hormozi.md`
  - Contrato creativo/estilístico activo. `copywriter_contract.md` queda como referencia histórica y puede reactivarse vía `STYLE_CONTRACT_PATH`.
- `config/final_review_guidelines.md`
  - Pautas complementarias anti-cliché/anti-IA aplicadas en la revisión final (modo "warden"): si no se cumplen, la generación se rechaza con feedback. Puedes cambiar el archivo con `FINAL_REVIEW_GUIDELINES_PATH`.

## Configuración y Entorno

- Python 3.10+.
- Instala con: `python -m venv venv && source venv/bin/activate && pip install -r requirements.txt`.
- Variables de entorno (.env, no subir nunca):
  - `GOOGLE_API_KEY` (obligatoria para embeddings y Gemini)
  - `OPENROUTER_API_KEY` (opcional; si falta, se usa Gemini)
  - `FALLBACK_PROVIDER_ORDER` (por defecto `gemini,openrouter`)
  - `GEMINI_MODEL` (por defecto `gemini-2.5-pro`)
  - `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (si usas el bot)
  - Overrides opcionales: `STYLE_CONTRACT_PATH`, `ICP_PATH`, `FINAL_REVIEW_GUIDELINES_PATH`

## Flujo de Datos (camino feliz)

1) Ingesta: PDF a `uploads/` → texto → chunks → extracción temas (JSON) → validación COO (JSON) → embeddings → `topics_collection`.
2) Generación: `core_generator.generate_tweet_from_topic(abstract)` produce `[EN - A]` y `[EN - B]` ≤ 280 (iterativo LLM), revisa similitud previa.
3) Bot: `/generate` → elige tema aleatorio, muestra A/B con tema+PDF+conteo, callbacks para aprobar y persistir en `memory_collection`.

## Políticas de LLM

- Orden de proveedores: `FALLBACK_PROVIDER_ORDER` (por defecto `gemini,openrouter`).
- Detección de fallo para fallback: mensajes que contengan `insufficient`, `quota`, `credit`, `billing`, o códigos `402/401/403/429`.
- JSON estricto:
  - OpenRouter: `response_format={"type":"json_object"}`.
  - Gemini: instrucción “ONLY strict JSON” + extracción robusta del primer bloque `{}`/`[]` si hubiese ruido.
- Modelos sugeridos:
  - OpenRouter: `anthropic/claude-3.5-sonnet` (generación) y `anthropic/claude-3-haiku` (refinado/validación).
  - Gemini: `gemini-2.5-pro` o `gemini-2.5-flash`.
- Límite 280 sin truncado local: usar helper de acortado iterativo vía LLM. Si no logra ≤ 280, reintentar la generación completa (no cortar).

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

- OpenRouter 402/ratelimits → verás logs “Falló OpenRouter… fallback a Gemini: …”. No fuerces reintentos inmediatos; deja al fallback trabajar.
- Gemini 404 model not found → usa `genai.list_models()` y ajusta `GEMINI_MODEL`.
- Telegram 400 “can’t parse entities” → ocurre por Markdown; ya hay fallback a texto plano. Considera escape MarkdownV2 si se requiere formato estable.
- Chroma “Could not connect to tenant …” → carrera en init; ya mitigado con lock. Si persiste, verifica permisos y la existencia de `db/`.

## Pruebas y Validación (rápido y seguro)

- Smoke tests sin red:
  - Monkeypatch `llm._openrouter_chat` para lanzar 402 y validar fallback a Gemini (stub) → ya existen ejemplos en desarrollo.
- Listing de modelos Gemini (real):
  - `import google.generativeai as genai; genai.configure(api_key=...); list(genai.list_models())`.
- Watcher local:
  - Ejecuta `python watcher_with_metadata.py`, copia un PDF pequeño a `uploads/` y verifica `json/` + `db/` + notificación.
- Bot:
  - Con `/generate` debe llegar propuesta con tema+PDF y conteos. Aprobación A/B debe crear intent URL de X y guardar en memoria.

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

- Circuit breaker de proveedor: si OpenRouter falla por créditos, memorizar el estado X minutos para no reintentar hasta entonces.
- Escape MarkdownV2 opcional: helper para evitar errores 400 sin renunciar a formato.
- Scheduler ligero (cron/apscheduler): generación programada de propuestas sin intervención manual.
- Métricas mínimas: contador de entregas, ratio de fallback, ratio de ≤280 a la primera, tiempos medios (logging agregable).
- Límite “objetivo” 260 en generación inicial para aumentar probabilidad de ≤280 tras refinado (configurable).
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
