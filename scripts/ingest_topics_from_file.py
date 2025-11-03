#!/usr/bin/env python3
"""
CLI para ingerir tópicos en el endpoint remoto /ingest_topics.

- Lee un archivo JSONL (una línea = un objeto JSON).
- Normaliza campos esperados por el endpoint: id, abstract, pdf/source_pdf.
- Mapea topic_id -> id automáticamente.
- Si solo existe "text", lo usa como "abstract".
- Publica en lotes al servicio remoto y muestra el resumen de la operación.

Uso:
  python scripts/ingest_topics_from_file.py \
    --file data/seeds/topics_sample.jsonl \
    --remote-url https://<tu-cloud-run-url> \
    --token <ADMIN_API_TOKEN> \
    --batch-size 256

Notas:
- El token puede omitirse si está en el entorno (ADMIN_API_TOKEN en .env).
- El endpoint remoto utiliza su propia TOPICS_COLLECTION y EMBED_MODEL.
- Para pasar health, el servicio remoto debe tener EMBED_MODEL con SIM_DIM=3072.
"""

import argparse
import json
import os
import sys
import time
from typing import Dict, List, Optional

try:
    import urllib.request as urlreq
    import urllib.error as urlerr
    import ssl as urssl
except Exception:
    print("ERROR: urllib no disponible", file=sys.stderr)
    sys.exit(1)


def _slugify(text: str, max_len: int = 48) -> str:
    """Crea un id simple a partir del texto si no viene uno. No garantiza unicidad global."""
    base = ''.join(ch.lower() if ch.isalnum() else '-' for ch in text.strip())
    base = '-'.join([p for p in base.split('-') if p])
    if len(base) > max_len:
        base = base[:max_len]
    return base or f"id-{int(time.time())}"


def _normalize_item(obj: Dict[str, object]) -> Optional[Dict[str, object]]:
    if not isinstance(obj, dict):
        return None
    tid = str(obj.get("id") or obj.get("topic_id") or "").strip()
    abstract = str(obj.get("abstract") or obj.get("text") or "").strip()
    pdf = str(obj.get("pdf") or obj.get("source_pdf") or "").strip() or None
    if not tid:
        # Genera un id a partir del texto cuando no viene
        if abstract:
            tid = _slugify(abstract)
        else:
            return None
    if not abstract:
        return None
    item: Dict[str, object] = {"id": tid, "abstract": abstract}
    if pdf:
        item["pdf"] = pdf
    return item


def _post_json(url: str, payload: Dict[str, object], timeout: int = 60) -> Dict[str, object]:
    data = json.dumps(payload).encode("utf-8")
    req = urlreq.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlreq.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body or "{}")
    except urlerr.HTTPError as e:
        msg = e.read().decode("utf-8") if hasattr(e, "read") else str(e)
        return {"ok": False, "error": f"http_error_{e.code}", "message": msg}
    except Exception as e:
        return {"ok": False, "error": f"request_failed", "message": str(e)}


def _get_json(url: str, timeout: int = 30) -> Dict[str, object]:
    req = urlreq.Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urlreq.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body or "{}")
    except urlerr.HTTPError as e:
        msg = e.read().decode("utf-8") if hasattr(e, "read") else str(e)
        return {"ok": False, "error": f"http_error_{e.code}", "message": msg}
    except Exception as e:
        return {"ok": False, "error": f"request_failed", "message": str(e)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True, help="Ruta del JSONL con tópicos")
    ap.add_argument("--remote-url", required=True, help="URL base del servicio (Cloud Run)")
    ap.add_argument("--token", default=os.getenv("ADMIN_API_TOKEN", ""), help="ADMIN_API_TOKEN")
    ap.add_argument("--batch-size", type=int, default=256, help="Tamaño del lote para POST")
    ap.add_argument("--insecure", action="store_true", help="Deshabilita verificación SSL (solo desarrollo)")
    args = ap.parse_args()

    if not args.token:
        print("ERROR: Falta --token o ADMIN_API_TOKEN en entorno", file=sys.stderr)
        return 2

    path = args.file
    if not os.path.exists(path):
        print(f"ERROR: No existe el archivo: {path}", file=sys.stderr)
        return 2

    items: List[Dict[str, object]] = []
    # 1) Intentar cargar como JSON completo
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        # data puede ser dict o list
        if isinstance(data, dict):
            # Caso: {"topics": [...]} o similar
            raw_items = data.get("topics")
            if isinstance(raw_items, list):
                for it in raw_items:
                    if isinstance(it, str):
                        norm = _normalize_item({"text": it})
                    elif isinstance(it, dict):
                        norm = _normalize_item(it)
                    else:
                        norm = None
                    if norm:
                        items.append(norm)
            else:
                # Intentar normalizar el propio dict como un solo ítem
                norm = _normalize_item(data)
                if norm:
                    items.append(norm)
        elif isinstance(data, list):
            for it in data:
                if isinstance(it, str):
                    norm = _normalize_item({"text": it})
                elif isinstance(it, dict):
                    norm = _normalize_item(it)
                else:
                    norm = None
                if norm:
                    items.append(norm)
        else:
            # Fallback: tratar como JSONL más abajo
            raise ValueError("Formato JSON no soportado; intentar JSONL")
    except Exception:
        # 2) Si falla JSON completo, intentar JSONL (una línea = objeto)
        with open(path, "r", encoding="utf-8") as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    obj = json.loads(ln)
                except Exception:
                    continue
                norm = _normalize_item(obj)
                if norm:
                    items.append(norm)

    if not items:
        print("No hay tópicos válidos para enviar.", file=sys.stderr)
        return 1

    print(f"Preparados {len(items)} tópicos. Publicando en lotes de {args.batch_size}…")
    added_total = 0
    skipped_total = 0
    errors_total = 0

    base = args.remote_url.rstrip("/")
    ingest_url = f"{base}/ingest_topics?token={args.token}"
    ctx = None
    if args.insecure:
        try:
            ctx = urssl._create_unverified_context()  # type: ignore[attr-defined]
        except Exception:
            ctx = None
    for i in range(0, len(items), args.batch_size):
        chunk = items[i:i + args.batch_size]
        payload = {"topics": chunk}
        # Permite contexto SSL inseguro para desarrollo
        if ctx:
            # Monkeypatch temporal del opener con contexto
            opener = urlreq.build_opener(urlreq.HTTPSHandler(context=ctx))
            urlreq.install_opener(opener)
        resp = _post_json(ingest_url, payload)
        ok = bool(resp.get("ok"))
        added = int(resp.get("added", 0))
        skipped = int(resp.get("skipped_existing", 0))
        errors = int(resp.get("errors", 0))
        added_total += added
        skipped_total += skipped
        errors_total += errors
        print(f"Lote {i//args.batch_size + 1}: ok={ok} added={added} skipped={skipped} errors={errors}")

    stats = _get_json(f"{base}/stats?token={args.token}")
    health = _get_json(f"{base}/health/embeddings")
    print("\nResumen final:")
    print(json.dumps({
        "ingest": {"added": added_total, "skipped_existing": skipped_total, "errors": errors_total},
        "stats": stats,
        "health": health,
    }, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
