#!/usr/bin/env python3
"""
Bootstrap or reset local workspace directories with reproducible seed data.

Usage:
    python scripts/bootstrap_workspace.py            # create dirs and copy seeds
    python scripts/bootstrap_workspace.py --clean    # wipe targets before seeding

This keeps dev machines consistent without committing generated artefacts
(`json/`, `texts/`, `uploads/`, etc.). Secrets stay in environment variables.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TARGET_JSON_DIR = REPO_ROOT / "json"
TARGET_TEXT_DIR = REPO_ROOT / "texts"
TARGET_UPLOADS_DIR = REPO_ROOT / "uploads"

SEED_TOPICS = REPO_ROOT / "data" / "seeds" / "topics_sample.jsonl"
SEED_TEXT = REPO_ROOT / "data" / "seeds" / "text_sample.txt"


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _clean_dir(path: Path) -> None:
    if not path.exists():
        return
    for entry in path.iterdir():
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()


def _copy_seed(src: Path, dst: Path) -> None:
    if not src.is_file():
        return
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")


def bootstrap(clean: bool) -> None:
    for directory in (TARGET_JSON_DIR, TARGET_TEXT_DIR, TARGET_UPLOADS_DIR):
        if clean:
            _clean_dir(directory)
        _ensure_dir(directory)

    # Seed sample topics/json output for quick smoke tests
    _copy_seed(SEED_TOPICS, TARGET_JSON_DIR / "sample_topics.jsonl")

    # Seed a deterministic text snippet useful for watcher smoke runs
    _copy_seed(SEED_TEXT, TARGET_TEXT_DIR / "sample_source.txt")

    # Uploads stay empty by default; drop a README hint for new developers.
    readme_path = TARGET_UPLOADS_DIR / "README.bootstrap"
    if not readme_path.exists():
        readme_path.write_text(
            "Drop PDFs to trigger watcher_app/watch_directory during local tests.\n",
            encoding="utf-8",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare local workspace folders with deterministic seeds.")
    parser.add_argument("--clean", action="store_true", help="Remove existing contents before seeding.")
    args = parser.parse_args()
    bootstrap(clean=args.clean)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
