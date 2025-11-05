"""Legacy wrapper to call build_goldset_npz with the old CLI (--output)."""

import argparse
import os
import subprocess
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Legacy wrapper; delega en build_goldset_npz.py")
    parser.add_argument("--output", required=False, default="data/gold_posts/goldset_embeddings.npz")
    parser.add_argument("--collection", default=os.getenv("GOLDSET_COLLECTION_NAME", "goldset_norm_v1"))
    parser.add_argument("--jsonl")
    parser.add_argument("--emb-model", default=os.getenv("EMB_MODEL", "openai/text-embedding-3-large"))
    parser.add_argument("--dim", type=int, default=int(os.getenv("EMB_DIM", "3072")))
    parser.add_argument("--normalizer-version", type=int, default=int(os.getenv("GOLDSET_NORMALIZER_VERSION", "1")))
    args = parser.parse_args()

    build_script = Path(__file__).resolve().with_name("build_goldset_npz.py")
    cmd = [
        sys.executable,
        str(build_script),
        "--out",
        args.output,
        "--collection",
        args.collection,
        "--emb-model",
        args.emb_model,
        "--dim",
        str(args.dim),
        "--normalizer-version",
        str(args.normalizer_version),
    ]
    if args.jsonl:
        cmd.extend(["--jsonl", args.jsonl])

    subprocess.run(cmd, check=True)


if __name__ == "__main__":
    main()
