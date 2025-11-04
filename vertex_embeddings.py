"""
Proveedor de embeddings usando Vertex AI.

Requiere:
- vertexai (SDK)
- Variables de entorno: GCP_PROJECT_ID, GCP_LOCATION

Modelo sugerido: "textembedding-gecko" o el que definas en EMBED_MODEL.
"""

import os
from typing import List, Optional
from logger_config import logger

_vertex_initialized = False

def _init_vertex() -> None:
    global _vertex_initialized
    if _vertex_initialized:
        return
    try:
        import vertexai
        project = os.getenv("GCP_PROJECT_ID")
        location = os.getenv("GCP_LOCATION", "us-central1")
        if not project:
            logger.warning("GCP_PROJECT_ID no configurado; usando credenciales por defecto del entorno.")
        vertexai.init(project=project, location=location)
        _vertex_initialized = True
    except Exception as e:
        logger.error("No se pudo inicializar Vertex AI: %s", e)
        _vertex_initialized = False


def get_vertex_embedding(text: str, model_name: Optional[str] = None) -> Optional[List[float]]:
    """Obtiene embedding desde Vertex AI para un texto.

    model_name: si no se pasa, intentar usar EMBED_MODEL o 'textembedding-gecko'.
    """
    _init_vertex()
    if not _vertex_initialized:
        return None
    try:
        from vertexai.preview.language_models import TextEmbeddingModel
        mn = model_name or os.getenv("EMBED_MODEL", "textembedding-gecko")
        mdl = TextEmbeddingModel.from_pretrained(mn)
        resp = mdl.get_embeddings([text])
        if not resp:
            logger.error("Vertex AI embeddings vacío")
            return None
        vec = getattr(resp[0], "values", None)
        if not isinstance(vec, list):
            logger.error("Vertex AI embedding vector inválido")
            return None
        return vec
    except Exception as e:
        logger.error("Error obteniendo embeddings desde Vertex AI: %s", e)
        return None

