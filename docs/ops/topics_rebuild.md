# Runbook: Rebuild de topics_collection (dim=3072)

Fecha: 2025-11-05
Autor: x_bot_mei
Propósito del cambio: Proveer un proceso determinista para reconstruir la colección de tópicos con embeddings 3072 y mantener paridad con el goldset.
Justificación: Asegurar continuidad operativa cuando el sistema usa con frecuencia el fallback de tópicos y garantizar salud y consistencia del índice.

## Cuándo reconstruir
- Si `fallback_used` > 20% en una ventana de 1 hora (telemetría `topic_fallback_used`).
- Cuando se detecte degradación de salud de la colección: dimensiones inconsistentes, conteo < 50, o errores de acceso en Chroma.
- Cambios en el modelo de embeddings que requieran recalcular (migración de modelo o actualización mayor).

## Validación previa
1. Validar que el endpoint `GET /collections/topics/health` responde `200`.
2. Confirmar que `emb_dim == 3072`.
3. Verificar que `count >= 50` (después de reconstrucción).
4. Revisar logs de `TOPICS_REBUILT {source,count,emb_dim}`.

## Procedimiento
1. Ejecutar el script de reconstrucción:
   - `python scripts/rebuild_topics.py --from auto`
   - Opciones: `--from seed|goldset|auto` (auto por defecto)
   - Fuentes seed incluidas automáticamente: `data/topics_seed.jsonl` y todos los `*.jsonl` bajo `data/seeds/` (deduplicación por `id` y orden determinista por nombre de archivo).
2. Confirmar salud:
   - `curl -s "$BASE_URL/collections/topics/health" | jq .`
3. Registrar el evento y verificación:
   - Revisar logs para `TOPICS_REBUILT` con `source`, `count` y `emb_dim`.
4. (Opcional, si trabajaste en local) Migrar a remoto:
   - `python scripts/migrate_local_chroma_to_remote.py --source-path .chroma_topics_tmp --dest-url https://<tu-chroma-host> --collections topics_collection_3072 --batch 256`
   - Verificar desde la app con `CHROMA_DB_URL` apuntando a remoto: `remote_count` y dimensiones.

## Notas de implementación
- El script usa el modelo `openai/text-embedding-3-large` (3072 dimensiones) y upserta a `topics_collection_3072`.
- Determinismo garantizado con `random.seed(17)`; muestreo estable del goldset.
- Se asegura `SIM_DIM=3072` y `TOPICS_COLLECTION=topics_collection_3072` si no están en `.env`.
- Seeds múltiples: se agregan todos los JSONL bajo `data/seeds/` además del `data/topics_seed.jsonl`. Se deduplican por `id` y se mantiene orden estable por nombre de archivo.
- En entornos remotos, usa `CHROMA_DB_URL` para apuntar la app al servidor HTTP de Chroma; la migración desde local es opcional si reconstruyes directamente apuntando a remoto.

## Checklist de validación post-rebuild
- [ ] Endpoint `/collections/topics/health` responde OK.
- [ ] `emb_dim == 3072`.
- [ ] `count >= 50`.
- [ ] Logs contienen `TOPICS_REBUILT` con valores coherentes.
- [ ] No hay errores en upsert ni warnings críticos en los logs.

## Historial de cambios
- 2025-11-05: Añadido runbook y script determinista de rebuild (autor: x_bot_mei). Justificación: incremento de fallback y necesidad de paridad con goldset.
- 2025-11-05: Actualizado para incluir seeds múltiples (`data/seeds/*.jsonl`), deduplicación por `id`, orden determinista y paso opcional de migración a Chroma remoto. Autor: x_bot_mei. Justificación: mayor cobertura y operación 100% remota.
