#!/usr/bin/env python3
"""
Fail if the repository tracks artefacts that must stay local (logs, caches, secrets).
Designed for CI and local pre-flight checks.
"""

from __future__ import annotations

import fnmatch
import subprocess
import sys
from pathlib import Path

FORBIDDEN_PATTERNS = [
    "*.log",
    "logs*.json",
    "*.sqlite3",
    ".admin_token_generated",
    "__pycache__/*",
    "json/*",
    "texts/*",
    "uploads/*",
    "processed_pdfs/*",
]


def _tracked_files() -> list[str]:
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        print(f"git ls-files failed: {exc}", file=sys.stderr)
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _violations(files: list[str]) -> list[str]:
    matches: list[str] = []
    repo_root = Path(__file__).resolve().parent.parent
    for pattern in FORBIDDEN_PATTERNS:
        for file in files:
            if fnmatch.fnmatch(file, pattern):
                if (repo_root / file).exists():
                    matches.append(file)
    return sorted(set(matches))


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    files = _tracked_files()
    offenders = _violations(files)
    if offenders:
        print("Found tracked artefacts that should stay local:", file=sys.stderr)
        for path in offenders:
            print(f"  - {path}", file=sys.stderr)
        print("\nRun `python scripts/bootstrap_workspace.py --clean` or delete them, then recommit.", file=sys.stderr)
        return 1
    print("Repository hygiene check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
