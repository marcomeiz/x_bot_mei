# 00 · Principios No Negociables y DoD

## Principios No Negociables
- No destruyas datos ni reescribas historial sin confirmación explícita.
- Cambios pequeños, enfocados y reversibles. No refactorices por deporte.
- HARD NO: nada hardcodeado. Si una constante puede ir a config, env var, prompt o storage externo, se mueve allí primero.
- Invariantes de negocio:
  - Tweets ≤ 280 sin recorte local (lo corrige el LLM, no el código local).
  - Mensajes de Telegram: tema (abstract), origen (PDF si existe) y contadores `(N/280)`.
- ChromaDB: los temas llevan metadatos `{"pdf": name}` si provienen de ingestión.
- Fallback LLM: orden por `FALLBACK_PROVIDER_ORDER` (por defecto Gemini → OpenRouter).

## Definición de Hecho (DoD)
Ningún cambio se aprueba sin:
- [ ] Cumplir contratos de interfaz (ver 04_CONTRACTS_API.md).
- [ ] Config externalizada: nada “hardcodeado” que deba vivir en config.
- [ ] Fallback probado: funciona si falta uno de los proveedores.
- [ ] Logs útiles: INFO en camino feliz; ERROR con traza cuando falla.
- [ ] Lean: sin dependencias innecesarias ni cambios fuera de alcance.

## Líneas Rojas (qué NO hacer)
- No cambiar contrato creativo ni guías finales sin aprobación.
- No quitar contadores `(N/280)` en Telegram.
- No romper `llm_fallback.py` sustituyéndolo por llamadas directas.
- No modificar colecciones/umbrales de Chroma sin revisar impacto.

## Checklist antes de mergear
- [ ] Plan claro y cambio acotado.
- [ ] Logs informativos añadidos/ajustados.
- [ ] ≤280 validado sin truncado local.
- [ ] Telegram ok o fallback a texto plano.
- [ ] Metadatos `pdf` preservados.
- [ ] Probado con `FALLBACK_PROVIDER_ORDER` actual.
- [ ] README/docs actualizadas si aplica.
