# 04 · Contratos de Interfaz (Invariantes y Firmas)

## Invariantes
- Tweets ≤ 280; sin truncado local.
- Mensajes Telegram con contadores `(N/280)` y origen cuando aplica.
- Metadatos `{"pdf": name}` para temas ingestados.

## Funciones clave
- generate_tweet_from_topic(abstract: str) -> dict
  - Devuelve `{"short": str, "mid": str, "long": str}` o `{ "error": "..." }`.
- find_relevant_topic() -> {"topic_id", "abstract", "source_pdf"?} | None
- find_topic_by_id(topic_id: str) -> {"topic_id", "abstract", "source_pdf"?} | None

## Modelos de Datos (orientativo)
Se recomienda tipar entradas/salidas con Pydantic para validar longitud y estructura.

