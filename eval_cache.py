import json
import os
import time
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, Optional


DEFAULT_TTL_SECONDS = int(os.getenv("EVAL_CACHE_TTL_SECONDS", "86400") or 86400)
CACHE_DIR = Path(os.getenv("EVAL_CACHE_DIR", ".cache"))
CACHE_PATH = CACHE_DIR / "eval_cache.json"


def _now() -> float:
    return time.time()


def _hash_text(text: str) -> str:
    normalized = (text or "").strip()
    return sha256(normalized.encode("utf-8")).hexdigest()


class EvalCache:
    """Tiny disk-backed cache for evaluation results.

    Stores only positive evaluations (approved=True) to allow early exit
    and avoid re-scoring identical texts.
    """

    def __init__(self, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self.ttl = max(0, int(ttl_seconds))
        self._store: Dict[str, Dict[str, Any]] = {}
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        try:
            if CACHE_PATH.exists():
                with open(CACHE_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        self._store = data
        except Exception:
            # Corrupt cache — start fresh
            self._store = {}
        self._loaded = True

    def _persist(self) -> None:
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            with open(CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(self._store, f, ensure_ascii=False, indent=2)
        except Exception:
            # Best-effort; skip errors
            pass

    def get(self, text: str) -> Optional[Dict[str, Any]]:
        """Return cached payload if still within TTL and approved."""
        self._load()
        key = _hash_text(text)
        entry = self._store.get(key)
        if not isinstance(entry, dict):
            return None
        ts = float(entry.get("ts", 0.0) or 0.0)
        approved = bool(entry.get("approved", False))
        if not approved:
            return None
        if self.ttl > 0 and _now() - ts > self.ttl:
            # Expired — delete and return None
            try:
                del self._store[key]
                self._persist()
            except Exception:
                pass
            return None
        return entry

    def put(self, text: str, payload: Dict[str, Any], approved: bool) -> None:
        """Store payload only if approved.

        Payload may include avg_fast_score, thresholds, reasons, etc.
        """
        if not approved:
            return
        self._load()
        key = _hash_text(text)
        self._store[key] = {
            "approved": True,
            "ts": _now(),
            **payload,
        }
        self._persist()


# Global singleton for convenience
eval_cache = EvalCache()

