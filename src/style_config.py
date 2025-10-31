import copy
import os
from functools import lru_cache
from pathlib import Path
from typing import Dict, Any

import yaml

from logger_config import logger


DEFAULT_STYLE_CONFIG: Dict[str, Any] = {
    "enforce": True,
    "revision_rounds": 2,
    "hedging_threshold": 1,
    "jargon_block_threshold": 1,
    "audit_jargon_score_min": 2,
    "audit_cliche_score_min": 2,
    "memory_similarity_floor": 0.35,
}


def _default_style_config_path() -> Path:
    override = os.getenv("STYLE_AUDIT_CONFIG_PATH")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / "config" / "style_audit.yaml"


@lru_cache(maxsize=1)
def _load_style_config() -> Dict[str, Any]:
    config = copy.deepcopy(DEFAULT_STYLE_CONFIG)
    path = _default_style_config_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        if not isinstance(data, dict):
            raise ValueError("Root YAML must be a mapping.")
        payload = data.get("style_audit") if "style_audit" in data else data
        if isinstance(payload, dict):
            for key, value in payload.items():
                if key in config:
                    config[key] = value
        else:
            logger.warning("style_audit config at %s must map keys to values; ignoring custom section.", path)
    except FileNotFoundError:
        logger.info("Style audit config not found at %s. Using defaults.", path)
    except Exception as exc:
        logger.warning("Failed to load style audit config from %s: %s. Using defaults.", path, exc)
    return config


def _env_bool(var_name: str, default: bool) -> bool:
    value = os.getenv(var_name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if not normalized:
        return default
    return normalized in {"1", "true", "yes", "y"}


def _env_int(var_name: str, default: int) -> int:
    value = os.getenv(var_name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning("Invalid integer for %s=%s. Keeping %s.", var_name, value, default)
        return default


def _env_float(var_name: str, default: float) -> float:
    value = os.getenv(var_name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        logger.warning("Invalid float for %s=%s. Keeping %s.", var_name, value, default)
        return default


def get_style_settings() -> Dict[str, Any]:
    cfg = _load_style_config()
    return {
        "enforce": _env_bool("ENFORCE_STYLE_AUDIT", bool(cfg.get("enforce", True))),
        "revision_rounds": _env_int("STYLE_REVISION_ROUNDS", int(cfg.get("revision_rounds", 2))),
        "hedging_threshold": _env_int("STYLE_HEDGING_THRESHOLD", int(cfg.get("hedging_threshold", 1))),
        "jargon_block_threshold": _env_int("STYLE_JARGON_BLOCK_THRESHOLD", int(cfg.get("jargon_block_threshold", 1))),
        "audit_jargon_score_min": _env_int("STYLE_AUDIT_JARGON_SCORE_MIN", int(cfg.get("audit_jargon_score_min", 2))),
        "audit_cliche_score_min": _env_int("STYLE_AUDIT_CLICHE_SCORE_MIN", int(cfg.get("audit_cliche_score_min", 2))),
        "memory_similarity_floor": _env_float(
            "STYLE_MEMORY_SIMILARITY_FLOOR", float(cfg.get("memory_similarity_floor", 0.35))
        ),
    }
