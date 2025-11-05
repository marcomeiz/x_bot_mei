#!/usr/bin/env python3
"""
Descarga un archivo desde Google Cloud Storage (gs://...) a una ruta local.

Uso:
  python scripts/gcs_fetch.py --uri gs://bucket/path/to/file.npz --out data/gold_posts/goldset_norm_v1.npz

Requisitos:
  - google-cloud-storage instalado
  - Credenciales GCP configuradas (ADC / Application Default Credentials)
"""

import argparse
from pathlib import Path
import os

def main():
    parser = argparse.ArgumentParser(description="Descargar archivo desde GCS a local")
    parser.add_argument("--uri", required=True, help="URI de GCS (gs://bucket/obj)")
    parser.add_argument("--out", required=True, help="Ruta local de salida")
    args = parser.parse_args()

    uri = args.uri
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        from google.cloud import storage
    except Exception as e:
        print(f"[ERROR] Falta dependencia google-cloud-storage: {e}")
        return 1

    if not uri.startswith("gs://"):
        print(f"[ERROR] URI inválida: {uri}")
        return 1
    parts = uri[5:].split("/", 1)
    if len(parts) != 2:
        print(f"[ERROR] URI incompleta: {uri}")
        return 1
    bucket_name, blob_path = parts
    client = storage.Client(project=os.getenv("GCP_PROJECT_ID"))
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_path)
    blob.download_to_filename(str(out_path))
    print(f"[GCS] Descargado {uri} -> {out_path}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

