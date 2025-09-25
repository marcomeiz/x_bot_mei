# embeddings_manager.py
import os
import google.generativeai as genai
import chromadb
from dotenv import load_dotenv

load_dotenv()

# La configuración de la API puede ser global
try:
    genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
except Exception as e:
    print(f"❌ Error configurando la API de Google: {e}")

# Patrón para asegurar una única instancia del cliente por proceso
_chroma_client = None

def get_chroma_client():
    """Devuelve una instancia única del cliente de ChromaDB, creándola si no existe."""
    global _chroma_client
    if _chroma_client is None:
        print("➡️  Inicializando nuevo cliente de ChromaDB para este proceso...")
        _chroma_client = chromadb.PersistentClient(path="db")
    return _chroma_client

def get_topics_collection():
    """Obtiene la colección de temas."""
    client = get_chroma_client()
    return client.get_or_create_collection(name="topics_collection", metadata={"hnsw:space": "cosine"})

def get_memory_collection():
    """Obtiene la colección de memoria."""
    client = get_chroma_client()
    return client.get_or_create_collection(name="memory_collection", metadata={"hnsw:space": "cosine"})

def get_embedding(text: str):
    """Genera el embedding para un texto dado."""
    try:
        result = genai.embed_content(model="models/embedding-001", content=text, task_type="RETRIEVAL_DOCUMENT")
        return result['embedding']
    except Exception as e:
        print(f"❌ Error al generar embedding: {e}")
        return None