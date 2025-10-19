from dataclasses import dataclass
import os
from typing import Iterable

from dotenv import load_dotenv


@dataclass(frozen=True)
class WatcherConfig:
    enforce_style_audit: bool
    lenient_validation: bool
    jargon_threshold: int
    cliche_threshold: int
    remote_ingest_url: str
    admin_api_token: str
    remote_batch: int
    remote_timeout: int
    remote_retries: int
    upload_dir: str
    text_dir: str
    json_dir: str


def load_config() -> WatcherConfig:
    load_dotenv()
    return WatcherConfig(
        enforce_style_audit=os.getenv("WATCHER_ENFORCE_STYLE_AUDIT", "0").lower() in ("1", "true", "yes", "y"),
        lenient_validation=os.getenv("WATCHER_LENIENT_VALIDATION", "1").lower() in ("1", "true", "yes", "y"),
        jargon_threshold=int(os.getenv("WATCHER_JARGON_THRESHOLD", "4") or 4),
        cliche_threshold=int(os.getenv("WATCHER_CLICHE_THRESHOLD", "4") or 4),
        remote_ingest_url=os.getenv("REMOTE_INGEST_URL", "").strip(),
        admin_api_token=os.getenv("ADMIN_API_TOKEN", "").strip(),
        remote_batch=int(os.getenv("REMOTE_INGEST_BATCH", "25") or 25),
        remote_timeout=int(os.getenv("REMOTE_INGEST_TIMEOUT", "120") or 120),
        remote_retries=int(os.getenv("REMOTE_INGEST_RETRIES", "3") or 3),
        upload_dir=os.getenv("UPLOAD_DIR", "uploads"),
        text_dir=os.getenv("TEXT_DIR", "texts"),
        json_dir=os.getenv("JSON_DIR", "json"),
    )


def ensure_directories(paths: Iterable[str]) -> None:
    for path in paths:
        os.makedirs(path, exist_ok=True)
