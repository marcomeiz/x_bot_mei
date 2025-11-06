#!/usr/bin/env python3
"""
Script para exportar todos los temas de ChromaDB a Google Sheets.
Ejecutar 1 vez al día via Cloud Scheduler para sincronizar.
"""
import os
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from embeddings_manager import get_topics_collection
from logger_config import logger
import json
from datetime import datetime

def export_topics_to_json():
    """Exporta todos los temas de ChromaDB a JSON."""
    try:
        topics = get_topics_collection()
        count = topics.count()
        logger.info(f"Found {count} topics in ChromaDB")

        # Get all topics (limit should be high enough)
        result = topics.get(
            limit=max(count, 1000),  # Safety margin
            include=['documents', 'metadatas']
        )

        # Build list of topics
        topics_list = []
        for i in range(len(result['ids'])):
            topic_id = result['ids'][i]
            abstract = result['documents'][i]
            metadata = result.get('metadatas', [{}])[i] or {}

            topics_list.append({
                'id': topic_id,
                'abstract': abstract,
                'source_pdf': metadata.get('source_pdf', ''),
                'approved': metadata.get('approved', False),
                'created_at': metadata.get('created_at', ''),
            })

        # Save to JSON
        output_file = 'data/topics_export.json'
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump({
                'exported_at': datetime.utcnow().isoformat(),
                'count': len(topics_list),
                'topics': topics_list
            }, f, indent=2, ensure_ascii=False)

        logger.info(f"Exported {len(topics_list)} topics to {output_file}")
        print(f"✅ Exported {len(topics_list)} topics to {output_file}")
        return topics_list

    except Exception as e:
        logger.error(f"Error exporting topics: {e}", exc_info=True)
        print(f"❌ Error: {e}")
        return []

if __name__ == '__main__':
    export_topics_to_json()
