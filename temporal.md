Objetivo: Garantizar un sistema de embeddings estable, coherente y alineado con la documentación, evitando generación indebida y errores de caché.

Problema: Errores de truthiness con arrays de NumPy en cargas desde ChromaDB y desalineación del orden de modelos de fallback respecto a la documentación.

Solución: 
- Se robustecieron las funciones de carga para convertir estructuras a listas de forma segura (evitando `or []` sobre arrays) y se aplanaron embeddings anidados.
- Se alineó el orden de modelos de fallback en `embeddings_manager.py` con lo indicado en la documentación.
- Se actualizó el CHANGELOG con los detalles de la corrección.

Verificación de duplicados: 
- Única implementación central de `get_embedding` en `embeddings_manager.py`. 
- El script `scripts/gen_goldset_npz.py` define un `get_embedding` local solo cuando `LOCAL_EMBED=1`, por diseño; no es duplicado funcional del runtime.

Estado: Perfecto.

Cambios realizados (referencias):
- embeddings_manager.py: `_chroma_load` → conversión segura a listas con `tolist()`/`list()` y flatten defensivo; reordenado `_embed_fallback_candidates`.
- src/goldset.py: `_load_embeddings_from_chroma` → conversión segura a listas y validación por tamaño.
- CHANGELOG.md: entrada de corrección y alineación.
