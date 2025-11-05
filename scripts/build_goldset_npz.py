# scripts/build_goldset_npz.py
# Construye un NPZ con textos + embeddings normalizados del goldset.
# Uso mínimo:
#   python scripts/build_goldset_npz.py --collection goldset_norm_v1 --out /tmp/goldset_norm_v1.npz
#
# Opcionalmente puedes pasar un JSONL con los textos:
#   python scripts/build_goldset_npz.py --jsonl data/goldset_norm_v1.jsonl --out /tmp/goldset_norm_v1.npz

import argparse, json, os, sys, time
from datetime import datetime, timezone
from pathlib import Path
import numpy as np

def _e(msg): print(msg, file=sys.stderr)

def load_texts(collection: str, jsonl_path: str|None):
    # 1) Si nos dan JSONL (id \t text), lo usamos
    if jsonl_path:
        texts, ids = [], []
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line=line.strip()
                if not line: continue
                try:
                    obj=json.loads(line)
                    ids.append(str(obj.get("id") or obj.get("piece_id") or len(ids)))
                    texts.append(str(obj["text"]))
                except Exception:
                    # fallback: formato TSV id \t text
                    if "\t" in line:
                        i,t = line.split("\t",1)
                        ids.append(i); texts.append(t)
                    else:
                        raise
        return ids, texts

    # 2) Intentamos cargar desde tu módulo goldset (si existe)
    try:
        # Debe exponer una función que devuelva lista de dicts con 'id' y 'text'
        from src import goldset as goldset_mod  # ajusta si tu paquete es distinto
        if hasattr(goldset_mod, "fetch_goldset_texts"):
            rows = goldset_mod.fetch_goldset_texts(collection)
        elif hasattr(goldset_mod, "load_goldset_texts"):
            rows = goldset_mod.load_goldset_texts(collection)
        else:
            raise ImportError("src.goldset no expone fetch_goldset_texts/load_goldset_texts")
        ids  = [str(r.get("id") or r.get("piece_id") or i) for i,r in enumerate(rows)]
        texts= [str(r["text"]) for r in rows]
        return ids, texts
    except Exception as e:
        _e(f"[ERR] No pude cargar textos del goldset '{collection}': {e}")
        _e("       Pasa --jsonl ruta/al/goldset.jsonl con {'id':..., 'text':...} por línea.")
        sys.exit(2)

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--collection", required=False, default=os.getenv("GOLDSET_COLLECTION_NAME","goldset_norm_v1"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--jsonl", required=False, help="JSONL opcional con {'id','text'} por línea")
    ap.add_argument("--emb-model", default=os.getenv("EMB_MODEL","openai/text-embedding-3-large"))
    ap.add_argument("--dim", type=int, default=int(os.getenv("EMB_DIM","3072")))
    ap.add_argument("--normalizer-version", type=int, default=int(os.getenv("GOLDSET_NORMALIZER_VERSION","1")))
    args = ap.parse_args()

    # Normalizador y embeddings del propio repo (no inventamos proveedores)
    from src.normalization import normalize_for_embedding

    try:
        from embeddings_manager import get_embedding
    except Exception as e:
        _e(f"[ERR] No encuentro embeddings_manager.get_embedding: {e}")
        sys.exit(2)

    ids, texts = load_texts(args.collection, args.jsonl)
    if not texts:
        _e("[ERR] Goldset vacío.")
        sys.exit(2)

    vecs = []
    t0 = time.time()
    for i, t in enumerate(texts, 1):
        nt = normalize_for_embedding(t)
        v = get_embedding(nt, model=args.emb_model)  # debe devolver lista/np.array
        if v is None:
            _e(f"[ERR] Embedding None en idx={i}")
            sys.exit(3)
        v = np.array(v, dtype=np.float32)
        if v.shape[0] != args.dim:
            _e(f"[ERR] Dimensión inesperada en idx={i}: {v.shape[0]} != {args.dim}")
            sys.exit(3)
        vecs.append(v)
        if i % 50 == 0: _e(f"[INFO] {i}/{len(texts)} embeddings...")

    E = np.stack(vecs, axis=0)  # [N, dim]
    meta = {
        "collection": args.collection,
        "emb_model": args.emb_model,
        "emb_dim": args.dim,
        "normalizer_version": args.normalizer_version,
        "count": int(E.shape[0]),
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    np.savez(args.out, ids=np.array(ids), texts=np.array(texts), embeddings=E, meta=json.dumps(meta))
    _e(f"[OK] NPZ escrito en {args.out} con {E.shape[0]} items. t={time.time()-t0:.1f}s")
    _e(f"[META] {meta}")

if __name__ == "__main__":
    main()
