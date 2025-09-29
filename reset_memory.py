import sys
import argparse

from embeddings_manager import get_chroma_client, get_memory_collection  # type: ignore


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Resetea por completo la colección 'memory_collection' en ChromaDB (acción irreversible).",
    )
    parser.add_argument(
        "--yes", "-y", action="store_true", help="No preguntar confirmación (usar con extremo cuidado)"
    )

    args = parser.parse_args(argv)

    coll = get_memory_collection()
    total = 0
    try:
        total = coll.count()  # type: ignore
    except Exception:
        total = 0

    if not args.yes:
        prompt = (
            f"¿Estás seguro de que quieres borrar los {total} elementos de 'memory_collection'?\n"
            "Esta acción es irreversible. [s/N]: "
        )
        ans = input(prompt).strip().lower()
        if ans not in ("s", "si", "sí", "y", "yes"):
            print("Operación cancelada.")
            return 0

    client = get_chroma_client()
    try:
        client.delete_collection(name="memory_collection")  # type: ignore
        # Recrear la colección vacía para asegurar disponibilidad inmediata
        client.get_or_create_collection(name="memory_collection", metadata={"hnsw:space": "cosine"})  # type: ignore
        print("✅ 'memory_collection' ha sido vaciada y recreada correctamente.")
        return 0
    except Exception as e:
        print(f"❌ Error al resetear 'memory_collection': {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

