Comparativa técnica de estrategias de embeddings y almacenamiento (selección óptima)

Objetivo
- Evitar recomputación redundante y minimizar costo/latencia con persistencia GCP y verificación previa.

Alternativas evaluadas
1) Solo LRU en memoria (proceso)
   - Complejidad: O(1) promedio por acceso; O(N) memoria
   - Latencia: ~0.1–0.5 ms
   - Persistencia: No; se pierde entre despliegues
   - Mantenimiento: Bajo; riesgo de pérdida ante escalado horizontal
2) Caché en sistema de archivos (FS)
   - Complejidad: O(1) por lookup (path hashing); I/O local
   - Latencia: ~1–3 ms (lectura .npy pequeña)
   - Persistencia: Parcial; no ideal en Cloud Run (ephemeral)
   - Mantenimiento: Medio; requiere volumen/montaje
3) ChromaDB embedding_cache (local/HTTP)
   - Complejidad: O(1) por id (get/upsert); index vector no necesario para lookup exacto
   - Latencia: ~3–15 ms local; ~10–30 ms remoto
   - Persistencia: Sí (si está configurado con almacenamiento duradero)
   - Mantenimiento: Medio; dependencia de servicio adicional
4) Firestore (GCP) + opcional GCS
   - Complejidad: O(1) por doc_id
   - Latencia: ~8–25 ms por lectura en región; ~10–35 ms escritura
   - Persistencia: Sí; TTL y versionado vía fingerprint
   - Mantenimiento: Bajo; servicio gestionado
5) Generación on-demand (Vertex/OpenRouter) sin verificación
   - Complejidad: O(M) por vector (dimensión M); costo variable
   - Latencia: ~80–800 ms/req según proveedor y carga
   - Persistencia: No
   - Mantenimiento: Bajo; costo elevado

Matriz comparativa (resumen)
- Complejidad algorítmica: LRU/FS/Firestore/Chroma = O(1) lookup; Generación = O(M)
- Consumo de recursos: LRU (RAM), FS (I/O local), Chroma (CPU/I/O servicio), Firestore (gestión externa), Generación (CPU/GPU proveedor)
- Tiempo de ejecución: LRU < FS < Chroma local < Firestore ≈ Chroma remoto < Generación
- Facilidad de mantenimiento: Firestore alta; LRU/FS media; Chroma media; Generación alta pero cara

Selección final
- Flujo cache-first con persistencia: LRU → Firestore → FS → Chroma → Generación.
  - Justificación cuantitativa: Minimiza latencia promedio (hits frecuentes) y reduce costo de generación (>90% de consultas sirven de caché tras backfill).
  - Persistencia y reusabilidad entre despliegues: Firestore/GCS garantizan continuidad y versionado por fingerprint.

Parámetros de la decisión
- Objetivo principal: reducir “Cache miss → Generando embedding” a <10% del total tras backfill inicial.
- Latencia objetivo de lectura: <25 ms 95p para Firestore.
- Coste objetivo: Embeddings de pago sólo en faltantes reales y batch en horarios de baja demanda.

Notas de mantenimiento
- Fingerprint por modelo asegura retrocompatibilidad; permite coexistencia de múltiples versiones.
- Firestore TTL configurable para renovar sólo cuando corresponda.
