
import chromadb
import os
from logger_config import logger

def initialize_database():
    db_path = "db"
    logger.info(f"Creando una base de datos ChromaDB vacía y limpia en '{db_path}'...")
    
    if os.path.exists(db_path):
        logger.warning(f"El directorio '{db_path}' ya existe. Se asumirá que está correctamente inicializado.")
        return

    try:
        os.makedirs(db_path, exist_ok=True)
        client = chromadb.PersistentClient(path=db_path)
        
        # Crear las colecciones fuerza la inicialización de las tablas
        client.get_or_create_collection(name="topics_collection")
        client.get_or_create_collection(name="memory_collection")
        
        logger.info("Base de datos inicializada con éxito con las colecciones 'topics_collection' y 'memory_collection'.")
    except Exception as e:
        logger.critical(f"No se pudo inicializar la base de datos local: {e}", exc_info=True)

if __name__ == "__main__":
    initialize_database()
