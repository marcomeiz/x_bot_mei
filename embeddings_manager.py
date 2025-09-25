import os
import google.generativeai as genai
import chromadb
from dotenv import load_dotenv

# --- CONFIGURACIÓN INICIAL ---

# Cargar variables de entorno (como las API keys)
load_dotenv()

# Configurar la API de Google
try:
    genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
    print("✅ API de Google configurada correctamente.")
except Exception as e:
    print(f"❌ Error configurando la API de Google. Asegúrate de que GOOGLE_API_KEY está en tu .env: {e}")

# --- CLIENTE DE LA BASE DE DATOS VECTORIAL ---

client = chromadb.PersistentClient(path="db")
print("✅ Cliente de ChromaDB inicializado.")


# --- NUEVA SECCIÓN: CREACIÓN DE COLECCIONES ---

try:
    # Colección para los temas extraídos de los PDFs
    topics_collection = client.get_or_create_collection(
        name="topics_collection",
        metadata={"hnsw:space": "cosine"} # Usamos 'cosine' para medir la similitud
    )

    # Colección para los tuits ya publicados (nuestra memoria a largo plazo)
    memory_collection = client.get_or_create_collection(
        name="memory_collection",
        metadata={"hnsw:space": "cosine"}
    )
    print("✅ Colecciones 'topics' y 'memory' cargadas/creadas con éxito.")
except Exception as e:
    print(f"❌ Error al crear/cargar las colecciones de ChromaDB: {e}")


# --- FUNCIÓN DE EMBEDDING ---

def get_embedding(text: str):
    """
    Genera el embedding para un texto dado usando la API de Google.
    """
    try:
        result = genai.embed_content(
            model="models/embedding-001",
            content=text,
            task_type="RETRIEVAL_DOCUMENT"
        )
        return result['embedding']
    except Exception as e:
        print(f"❌ Error al generar embedding: {e}")
        return None

# --- BLOQUE DE PRUEBA ---
# (Lo mantenemos por si necesitamos hacer pruebas más adelante)
if __name__ == "__main__":
    print("\n--- Módulo embeddings_manager cargado ---")
    print(f"   - Temas en la colección 'topics': {topics_collection.count()}")
    print(f"   - Tuits en la colección 'memory': {memory_collection.count()}")
    print("--- El sistema está listo para el siguiente paso ---")