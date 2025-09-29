import os
import google.generativeai as genai
import threading
import chromadb
from dotenv import load_dotenv

# --- NUEVO: Importar el logger configurado ---
from logger_config import logger

load_dotenv()

# La configuración de la API puede ser global
try:
    genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
except Exception as e:
    # --- MODIFICADO: Usar el logger para registrar el error ---
    logger.critical(f"No se pudo configurar la API de Google. El programa no puede continuar. Error: {e}", exc_info=True)
    # Considera salir del programa si esta configuración es crítica
    # exit(1)

# Patrón para asegurar una única instancia del cliente por proceso
_chroma_client = None
_chroma_lock = threading.Lock()

def get_chroma_client():
    """Devuelve una instancia única del cliente de ChromaDB, creándola si no existe."""
    global _chroma_client
    if _chroma_client is None:
        with _chroma_lock:
            if _chroma_client is None:
                try:
                    db_path = os.getenv("CHROMA_DB_PATH", "db")
                    logger.info(f"Inicializando nuevo cliente persistente de ChromaDB (path='{db_path}')...")
                    os.makedirs(db_path, exist_ok=True)
                    _chroma_client = chromadb.PersistentClient(path=db_path)
                except Exception as e:
                    logger.error(f"Error inicializando ChromaDB persistente: {e}", exc_info=True)
                    # Reintento único tras asegurar el directorio
                    try:
                        db_path = os.getenv("CHROMA_DB_PATH", "db")
                        os.makedirs(db_path, exist_ok=True)
                        _chroma_client = chromadb.PersistentClient(path=db_path)
                    except Exception as e2:
                        logger.critical(f"Fallo crítico creando cliente ChromaDB: {e2}", exc_info=True)
                        raise
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
        # --- NUEVO: Registrar la llamada a la API ---
        logger.info(f"Generando embedding para texto: '{text[:50]}...'")
        result = genai.embed_content(model="models/embedding-001", content=text, task_type="RETRIEVAL_DOCUMENT")
        return result['embedding']
    except Exception as e:
        # --- NUEVO: Registrar el error específico de la API ---
        logger.error(f"Error al llamar a la API de Google para generar embedding: {e}", exc_info=True)
        return None
