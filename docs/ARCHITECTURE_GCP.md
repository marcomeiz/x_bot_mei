Título: Arquitectura GCP-nativa para embeddings persistentes

Resumen
- Sistema cache-first: LRU → Firestore → FS → Chroma → Generación (Vertex/OpenRouter)
- Persistencia y verificación por fingerprint de modelo
- Despliegue en Cloud Run, backfills con Cloud Run Jobs/Cloud Build

Componentes
- Vertex AI Embeddings (proveedor primario en Ruta B)
- Firestore: almacenamiento persistente de vectores y metadatos
- Cloud Storage (opcional): auditoría de npy y backups
- ChromaDB: base de conocimiento existente (topics/memory)
- Cloud Logging: logs estructurados y métricas
- Cloud Monitoring: dashboards/alertas (configuración sugerida)

Flujos principales
1) get_embedding(text)
   - lookup LRU → Firestore → FS → Chroma
   - si miss: generar → persistir (Firestore/FS/Chroma) → retornar
2) Backfill
   - scripts/backfill_firestore_from_chroma.py
   - Cloud Run Job/Cloud Build con variables: CHROMA_DB_URL/CHROMA_DB_PATH, GCP_PROJECT_ID, EMBED_MODEL

Métricas/KPIs (metrics.py)
- emb_cache_lookup(ms) por etapa
- emb_cache_hit(stage)
- emb_generate(ms, provider/model)
- emb_success(dim, fallback?)
- emb_failure(provider/model)

Pruebas y validación
- tests/test_embeddings_cache_gcp.py: hits de caché y aislamiento de fingerprint
- Carga: usar locust/k6 (pendiente) sobre endpoints críticos
- Benchmarks: comparar latencia y costo vs re-embedder (cloudbuild_reembed.yaml)

Escalamiento y mantenimiento
- Horizontal en Cloud Run: stateless, Firestore como punto de verdad
- TTL opcional en Firestore por colecciones de caches
- Rotación de fingerprint al cambiar de modelo/dimensión
- Observabilidad: dashboards en Monitoring y alertas por emb_failure

Troubleshooting
- Latencias altas: revisar emb_generate y proveedor
- Dimensiones no coinciden: validar configuración y modelos, re-embedder
- Duplicados: verificar fingerprint y claves normalizadas

