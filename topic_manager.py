"""
Gestión de temas: agregar, listar, aprobar.
"""
import os
import re
from typing import Optional, Dict
from datetime import datetime

from embeddings_manager import get_topics_collection, get_embedding
from logger_config import logger


def generate_topic_id(abstract: str) -> str:
    """Genera ID único para un tema basado en timestamp y texto."""
    # Sanitize abstract for ID (first 3 words, lowercase, no spaces)
    words = re.findall(r'\w+', abstract.lower())
    prefix = '-'.join(words[:3]) if words else 'topic'

    # Add timestamp for uniqueness
    timestamp = datetime.utcnow().strftime('%Y%m%d%H%M%S')

    return f"{prefix}-{timestamp}"


def add_topic(abstract: str, source: str = 'telegram', approved: bool = False) -> Dict[str, object]:
    """Agrega un nuevo tema a ChromaDB con embedding.

    Args:
        abstract: Texto del tema
        source: Fuente del tema (telegram, google_sheets, pdf, etc)
        approved: Si el tema ya está pre-aprobado

    Returns:
        Dict con resultado: {
            'success': bool,
            'topic_id': str,
            'message': str,
            'error': Optional[str]
        }
    """
    abstract = abstract.strip()

    if not abstract:
        return {
            'success': False,
            'topic_id': None,
            'message': 'El tema no puede estar vacío',
            'error': 'empty_abstract'
        }

    if len(abstract) < 20:
        return {
            'success': False,
            'topic_id': None,
            'message': 'El tema debe tener al menos 20 caracteres',
            'error': 'too_short'
        }

    if len(abstract) > 500:
        return {
            'success': False,
            'topic_id': None,
            'message': 'El tema no puede exceder 500 caracteres',
            'error': 'too_long'
        }

    try:
        # Generate ID
        topic_id = generate_topic_id(abstract)

        # Check if already exists (by similarity)
        topics = get_topics_collection()
        embedding = get_embedding(abstract, generate_if_missing=True)

        if not embedding:
            return {
                'success': False,
                'topic_id': None,
                'message': 'Error generando embedding',
                'error': 'embedding_failed'
            }

        # Query for similar topics
        similar = topics.query(
            query_embeddings=[embedding],
            n_results=1
        )

        # Check if too similar to existing (cosine distance < 0.1 = very similar)
        if similar['ids'] and similar['distances'] and similar['distances'][0]:
            distance = similar['distances'][0][0]
            if distance < 0.1:
                existing_id = similar['ids'][0][0]
                return {
                    'success': False,
                    'topic_id': None,
                    'message': f'Tema muy similar ya existe: {existing_id}',
                    'error': 'duplicate',
                    'existing_id': existing_id,
                    'distance': distance
                }

        # Add to ChromaDB
        topics.add(
            ids=[topic_id],
            documents=[abstract],
            embeddings=[embedding],
            metadatas=[{
                'source': source,
                'approved': approved,
                'created_at': datetime.utcnow().isoformat(),
                'source_pdf': '',
            }]
        )

        logger.info(f"✅ Topic added: {topic_id} (source: {source})")

        return {
            'success': True,
            'topic_id': topic_id,
            'message': f'✅ Tema agregado con ID: {topic_id}',
            'error': None
        }

    except Exception as e:
        logger.error(f"Error adding topic: {e}", exc_info=True)
        return {
            'success': False,
            'topic_id': None,
            'message': f'Error agregando tema: {str(e)}',
            'error': 'exception'
        }


def get_topics_count() -> int:
    """Retorna el número total de temas en ChromaDB."""
    try:
        topics = get_topics_collection()
        return topics.count()
    except Exception as e:
        logger.error(f"Error getting topics count: {e}")
        return 0


def list_recent_topics(limit: int = 10) -> list[Dict[str, str]]:
    """Lista los temas más recientes.

    Returns:
        List of dicts: [{'id': str, 'abstract': str, 'created_at': str}, ...]
    """
    try:
        topics = get_topics_collection()
        result = topics.get(
            limit=limit,
            include=['documents', 'metadatas']
        )

        topics_list = []
        for i in range(len(result['ids'])):
            metadata = result.get('metadatas', [{}])[i] or {}
            topics_list.append({
                'id': result['ids'][i],
                'abstract': result['documents'][i],
                'created_at': metadata.get('created_at', ''),
                'source': metadata.get('source', ''),
            })

        # Sort by created_at descending
        topics_list.sort(key=lambda x: x.get('created_at', ''), reverse=True)

        return topics_list[:limit]

    except Exception as e:
        logger.error(f"Error listing topics: {e}")
        return []
