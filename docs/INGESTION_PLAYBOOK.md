Guía de ingestión de tópicos y verificación de embeddings (Cloud Run)

Objetivo
- Tener el servicio remoto sano (ok=true) con SIM_DIM=3072 en goldset y topics.
- Ingerir tópicos desde archivos JSONL/CSV/JSON o seeds reproducibles.

Variables clave
- ADMIN_API_TOKEN: ya está en .env
- EMBED_MODEL: debe ser de 3072 dimensiones en Cloud Run
- SIM_DIM: 3072
- TOPICS_COLLECTION: usa la colección remota (ej. topics_collection_3072)
- WARMUP_ANCHORS: 150–200
- URL del servicio (Cloud Run): se obtiene con gcloud

Cómo obtener la URL de Cloud Run
1) gcloud run services describe x-bot-mei --region <tu-region> --format 'value(status.url)'
2) Guarda esa URL como MEI_URL si lo deseas (opcional): export MEI_URL=$(gcloud run services describe x-bot-mei --region <tu-region> --format 'value(status.url)')

Asegurar 3072 dimensiones en el servicio
- Recomendado: EMBED_MODEL=jinaai/jina-embeddings-v2-base-en (3072)
- Comando:
  gcloud run services update x-bot-mei --region <tu-region> \
    --update-env-vars EMBED_MODEL=jinaai/jina-embeddings-v2-base-en,SIM_DIM=3072,TOPICS_COLLECTION=topics_collection_3072,WARMUP_ANCHORS=150

Endpoint de ingestión (contrato)
- POST /ingest_topics?token=<ADMIN_API_TOKEN>
- Body: { "topics": [ { "id": "...", "abstract": "...", "pdf": "opcional" } ] }
- También acepta source_pdf en lugar de pdf.
- Si existe topic_id, hay que mapearlo a id.

Opción rápida: seeds reproducibles
- Archivo: data/seeds/topics_sample.jsonl
- Ingestión con el nuevo CLI:
  python scripts/ingest_topics_from_file.py \
    --file data/seeds/topics_sample.jsonl \
    --remote-url "$MEI_URL" \
    --token "$ADMIN_API_TOKEN"

Ingestión desde archivos propios
- Formatos admitidos por el CLI (JSONL):
  - {"id": "...", "abstract": "...", "pdf": "..."}
  - {"topic_id": "...", "abstract": "...", "source_pdf": "..."}
  - {"text": "...", "source": "..."}  → el CLI usará text como abstract y generará id
- Comando general:
  python scripts/ingest_topics_from_file.py \
    --file /ruta/absoluta/a/tus_topics.jsonl \
    --remote-url "$MEI_URL" \
    --token "$ADMIN_API_TOKEN" \
    --batch-size 256

Verificaciones post-ingestión
- Stats:
  curl -s "$MEI_URL/stats?token=$ADMIN_API_TOKEN"
- Health embeddings:
  curl -s "$MEI_URL/health/embeddings"
- Debe mostrar: goldset_dim=3072, topics_dim=3072, warmed≥WARMUP_ANCHORS, ok=true

PDFs ingresados (resumen)
- curl -s "$MEI_URL/pdfs?token=$ADMIN_API_TOKEN"
- Devuelve conteos por source_pdf y total de tópicos.

Calibración del umbral de similitud
- Script: scripts/calibrate_similarity.py
- Ejemplo:
  python scripts/calibrate_similarity.py \
    --collection goldset_collection \
    --thres-init 0.75 \
    --samples 5000
- Actualiza UMBRAL_SIMILITUD_LINE en .env con el valor sugerido.

Migración desde una base local de Chroma (local_topics_db)
- Script: scripts/migrate_local_chroma_to_remote.py
- Parmetros clave:
  - --local-path /ruta/a/local_topics_db
  - --remote-url "$MEI_URL"
  - --token "$ADMIN_API_TOKEN"
  - --batch-size 256
- Nota: si ves errores de compatibilidad (schemas antiguos), usa la herramienta oficial de migración de Chroma o re-embed con scripts/reembed_chroma_collections.py hacia 3072.

Checklist para no fallar
1) Cloud Run con EMBED_MODEL 3072 y SIM_DIM=3072
2) ADMIN_API_TOKEN presente
3) Ingestar al menos 2 tópicos de prueba
4) Health ok=true y warmed≥150
5) Calibrar UMBRAL_SIMILITUD_LINE

Con esto, no deberías necesitar proporcionar manualmente rutas o tokens cada vez: el token ya está en .env y la URL se obtiene con gcloud en un comando único.

