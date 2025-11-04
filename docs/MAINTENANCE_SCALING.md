Título: Protocolos de mantenimiento y escalamiento horizontal

Versionado y retrocompatibilidad
- Control de versiones estricto vía etiquetas de fingerprint (modelo, dims, proveedor)
- Mantener compatibilidad leyendo primero por fingerprint actual; fallback opcional a fingerprint anterior
- Documentar cambios en CHANGELOG.md con justificación técnica

Optimización de recursos
- Limitar tamaño de LRU en memoria según límites de Cloud Run
- Firestore: índices selectivos, TTL en colecciones de cache
- Re-embedding batch nocturno para homogenizar dimensiones y reducir latencia en horas pico

Escalamiento horizontal
- Mínimo de 2 réplicas, autoescalado por RPS
- evitar estado compartido en memoria (usar Firestore como fuente)
- Cloud Run Jobs para tareas batch (backfills, limpieza)

Monitoreo y alertas
- Dashboards en Cloud Monitoring con métricas de metrics.py
- Alertas por emb_failure y por latencia p95 de emb_generate

Procedimientos
- Cambio de modelo: incrementar versión/fingerprint, ejecutar re-embedder, validar dimensiones
- Rollback: restaurar fingerprint anterior, pausar generación con force=False hasta completar backfill
- Limpieza: eliminar entradas obsoletas por TTL o jobs de garbage collection

