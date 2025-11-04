"""
Convierte un JSON de goldset (lista de objetos con "text") a JSONL con {"id","text"} por línea.

Uso:
  python scripts/prepare_goldset_jsonl.py \
    --input data/gold_posts/hormozi_master.json \
    --output /tmp/goldset_norm_v1.jsonl \
    --id-prefix gold_ --pad 4
"""

import argparse
import json
from pathlib import Path


def normalize_text(s: str) -> str:
    s = (s or "").replace("\r", "\n")
    # colapsa espacios múltiples preservando saltos de línea
    lines = [" ".join(line.strip().split()) for line in s.splitlines()]
    out = "\n".join([l for l in lines if l])
    return out.strip()


def convert(input_path: Path, output_path: Path, id_prefix: str, pad: int) -> int:
    data = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("El JSON de entrada debe ser una lista de objetos")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with output_path.open("w", encoding="utf-8") as out:
        for i, item in enumerate(data, 1):
            if not isinstance(item, dict):
                continue
            text = normalize_text(str(item.get("text", "")))
            if not text:
                continue
            rid = f"{id_prefix}{i:0{pad}d}"
            out.write(json.dumps({"id": rid, "text": text}, ensure_ascii=False) + "\n")
            n += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--id-prefix", default="gold_")
    ap.add_argument("--pad", type=int, default=4)
    args = ap.parse_args()

    count = convert(Path(args.input), Path(args.output), args.id_prefix, args.pad)
    print(f"[OK] Escrito {args.output} con {count} líneas.")


if __name__ == "__main__":
    main()

