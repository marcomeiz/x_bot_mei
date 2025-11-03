"""
Migra colecciones de Chroma desde un almacén local (duckdb+parquet) a un servidor remoto HTTP.

Uso típico:
  python scripts/migrate_local_chroma_to_remote.py \
    --source-path ./db \
    --dest-url https://x-chroma-295511624125.europe-west1.run.app \
    --collections topics_collection_3072,memory_collection \
    --batch 256

Notas:
- Copia IDs, documentos, metadatos y embeddings tal cual (sin re-embed). Es rápido y barato.
- Requiere que todas las inserciones en la colección de destino tengan la misma dimensión de embedding.
- Si necesitas cambiar dimensión (p.ej. 1536 → 3072), usa primero scripts/reembed_chroma_collections.py contra el origen
  para crear una colección nueva con sufijo (ej: topics_collection_3072) y luego migra esa a remoto.
"""

import argparse
from urllib.parse import urlparse
from typing import Any, Dict, List, Tuple

import chromadb
from chromadb.config import Settings as ChromaSettings


def _flatten_list(x: Any) -> List[Any]:
    """Aplanar listas anidadas como [[a,b], [c]] a [a,b,c]."""
    if isinstance(x, list) and x and isinstance(x[0], list):
        out: List[Any] = []
        for sub in x:
            out.extend(sub)
        return out
    return x if isinstance(x, list) else []


def _sanitize_metadatas(metas: List[Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for m in metas or []:
        if isinstance(m, list):
            # casos de respuestas anidadas
            m = (m[0] if m else {})
        if not isinstance(m, dict):
            m = {}
        # Chroma requiere dict con valores primitivos; forzamos strings vacíos para claves conocidas
        clean: Dict[str, Any] = {}
        for k, v in m.items():
            if isinstance(v, (str, int, float, bool)) or v is None:
                clean[k] = v if v is not None else ""
            else:
                clean[k] = str(v)
        out.append(clean)
    return out


def _read_page(coll, limit: int, offset: int) -> Tuple[List[str], List[str], List[List[float]], List[Dict[str, Any]]]:
    data = coll.get(include=["documents", "embeddings", "metadatas"], limit=limit, offset=offset) or {}
    ids = _flatten_list(data.get("ids") or [])
    docs = _flatten_list(data.get("documents") or [])
    embeds = _flatten_list(data.get("embeddings") or [])
    metas_raw = data.get("metadatas") or []
    metas = _sanitize_metadatas(_flatten_list(metas_raw))
    # Normalizar tipos finales
    ids = [str(i) for i in ids]
    docs = [str(d[0]) if isinstance(d, list) else str(d) for d in docs]
    return ids, docs, embeds, metas


def migrate_collection(source_client, dest_client, name: str, batch: int) -> None:
    src = source_client.get_or_create_collection(name=name, metadata={"hnsw:space": "cosine"})
    dest = dest_client.get_or_create_collection(name=name, metadata={"hnsw:space": "cosine"})

    # Intentar inferir dimensión desde una muestra del origen
    sample = src.get(include=["embeddings"], limit=1) or {}
    emb = _flatten_list(sample.get("embeddings") or [])
    dim = (len(emb[0]) if emb else None)
    print(f"[INFO] Migrando colección '{name}' (dim_origen={dim}) en lotes de {batch}…")

    total_src = src.count() or 0
    copied = 0
    offset = 0
    while True:
        ids, docs, embeds, metas = _read_page(src, limit=batch, offset=offset)
        if not ids:
            break
        # Subdividir por seguridad en chunks <= batch
        for start in range(0, len(ids), batch):
            end = start + batch
            ids_c = ids[start:end]
            docs_c = docs[start:end]
            embeds_c = embeds[start:end]
            metas_c = metas[start:end]
            dest.upsert(ids=ids_c, documents=docs_c, embeddings=embeds_c, metadatas=metas_c)
            copied += len(ids_c)
        offset += len(ids)
        print(f"[INFO] Copiados {copied}/{total_src}…")

    # Verificación ligera en destino
    try:
        dcount = dest.count()
        dsample = dest.get(include=["embeddings"], limit=1) or {}
        demb = _flatten_list(dsample.get("embeddings") or [])
        ddim = (len(demb[0]) if demb else None)
        print(f"[INFO] Destino '{name}': count={dcount} dim_muestra={ddim}")
    except Exception as e:
        print(f"[WARN] Verificación destino falló: {e}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrar colecciones Chroma de local a remoto")
    parser.add_argument("--source-path", required=True, help="Ruta del almacén local (persist_directory) p.ej. ./db")
    parser.add_argument("--dest-url", required=True, help="URL del servidor remoto Chroma, p.ej. https://host.run.app")
    parser.add_argument("--collections", default="topics_collection_3072", help="Lista separada por comas de colecciones a migrar")
    parser.add_argument("--batch", type=int, default=256, help="Tamaño de lote para lectura/escritura")
    args = parser.parse_args()

    # Cliente origen (local)
    # Cliente origen: usar PersistentClient para evitar configuraciones legacy
    source_client = chromadb.PersistentClient(path=args.source_path)

    # Cliente destino (HTTP)
    parsed = urlparse(args.dest_url)
    host = parsed.hostname or args.dest_url
    port = parsed.port or (443 if (parsed.scheme or "http").lower() == "https" else 80)
    ssl = (parsed.scheme or "http").lower() == "https"
    dest_client = chromadb.HttpClient(
        host=host, port=port, ssl=ssl, settings=ChromaSettings(anonymized_telemetry=False)
    )

    names = [n.strip() for n in args.collections.split(",") if n.strip()]
    for name in names:
        migrate_collection(source_client, dest_client, name, batch=args.batch)


if __name__ == "__main__":
    main()
