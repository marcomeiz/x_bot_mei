import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field

class VariantLength(BaseModel):
    min: Optional[int] = None
    max: int

class VariantLengths(BaseModel):
    short: VariantLength
    mid: VariantLength
    long: VariantLength

DEFAULT_VARIANT_LENGTHS = VariantLengths(
    short=VariantLength(max=150),
    mid=VariantLength(min=180, max=220),
    long=VariantLength(min=240, max=280),
)

class AppSettings(BaseModel):
    # LLM provider (OpenRouter-only)
    openrouter_api_key: Optional[str] = Field(default=os.getenv("OPENROUTER_API_KEY"))
    openrouter_base_url: str = Field(default=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"))

    # Per-purpose model choices (cheap by default)
    post_model: str = Field(default=os.getenv("POST_MODEL", "x-ai/grok-4-fast"))
    post_refiner_model: str = Field(default=os.getenv("POST_REFINER_MODEL", os.getenv("POST_MODEL", "x-ai/grok-4-fast")))
    post_temperature: float = Field(default=float(os.getenv("POST_TEMPERATURE", "0.6") or 0.6))
    post_preset: Optional[str] = Field(default=os.getenv("POST_PRESET"))
    eval_fast_model: str = Field(default=os.getenv("EVAL_FAST_MODEL", "qwen/qwen-2.5-7b-instruct"))
    eval_slow_model: str = Field(default=os.getenv("EVAL_SLOW_MODEL", "mistralai/mistral-nemo"))
    comment_audit_model: str = Field(default=os.getenv("COMMENT_AUDIT_MODEL", "qwen/qwen-2.5-7b-instruct"))
    comment_rewrite_model: str = Field(default=os.getenv("COMMENT_REWRITE_MODEL", "mistralai/mistral-nemo"))
    topic_extraction_model: str = Field(default=os.getenv("TOPIC_EXTRACTION_MODEL", "mistralai/mistral-nemo"))
    embed_model: str = Field(default=os.getenv("EMBED_MODEL", "openai/text-embedding-3-small"))

    # Paths
    prompts_dir: str = Field(default=os.getenv("PROMPTS_DIR", "prompts"))
    config_path: Optional[str] = Field(default=os.getenv("APP_CONFIG_PATH"))

    # Feature flags
    log_prompts: bool = Field(default=os.getenv("LOG_PROMPTS", "0").lower() in {"1", "true", "yes"})
    log_prompts_full: bool = Field(default=os.getenv("LOG_PROMPTS_FULL", "0").lower() in {"1", "true", "yes"})
    log_provider_decisions: bool = Field(default=os.getenv("LOG_PROVIDER_DECISIONS", "0").lower() in {"1", "true", "yes"})

    # Variant generation settings
    variant_lengths: VariantLengths = Field(default=DEFAULT_VARIANT_LENGTHS)

    @classmethod
    def load(cls) -> "AppSettings":
        path = os.getenv("APP_CONFIG_PATH") or _resolve_default_config_path()
        base = cls()
        base = _apply_post_preset(base)
        if not path:
            return base
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
            if not isinstance(data, dict):
                return base
            
            env_map = {
                "openrouter_base_url": "OPENROUTER_BASE_URL",
                "post_model": "POST_MODEL",
                "eval_fast_model": "EVAL_FAST_MODEL",
                "eval_slow_model": "EVAL_SLOW_MODEL",
                "comment_audit_model": "COMMENT_AUDIT_MODEL",
                "comment_rewrite_model": "COMMENT_REWRITE_MODEL",
                "topic_extraction_model": "TOPIC_EXTRACTION_MODEL",
                "embed_model": "EMBED_MODEL",
                "prompts_dir": "PROMPTS_DIR",
                "log_prompts": "LOG_PROMPTS",
                "log_prompts_full": "LOG_PROMPTS_FULL",
                "log_provider_decisions": "LOG_PROVIDER_DECISIONS",
                "post_temperature": "POST_TEMPERATURE",
                "post_preset": "POST_PRESET",
            }
            merged = base.model_dump()
            for k, v in data.items():
                # Nested structures like variant_lengths are handled separately
                if k == "variant_lengths":
                    continue
                env_name = env_map.get(k)
                env_set = bool(env_name and os.getenv(env_name))
                if not env_set:
                    merged[k] = v
            
            # Manually merge nested variant_lengths from file
            if "variant_lengths" in data:
                merged["variant_lengths"] = data["variant_lengths"]

            obj = cls(**merged)
            return _apply_post_preset(obj)
        except Exception:
            return base


def _resolve_default_config_path() -> Optional[str]:
    """Infer a config file path when APP_CONFIG_PATH is unset."""
    repo_root = Path(__file__).resolve().parent.parent
    config_dir = repo_root / "config"
    env_key = os.getenv("APP_CONFIG_ENV", "dev").strip().lower()
    candidates = []
    if env_key:
        candidates.append(config_dir / f"settings.{env_key}.yaml")
    candidates.append(config_dir / "settings.yaml")
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def _apply_post_preset(settings: AppSettings) -> AppSettings:
    """If POST_PRESET is set, adjust model and temperature accordingly, unless POST_MODEL/POST_TEMPERATURE are explicitly provided."""
    preset = (settings.post_preset or "").strip().lower()
    if not preset:
        return settings
    explicit_model = bool(os.getenv("POST_MODEL"))
    explicit_temp = bool(os.getenv("POST_TEMPERATURE"))
    model = settings.post_model
    temp = settings.post_temperature
    if preset == "speed":
        if not explicit_model:
            model = "qwen/qwen-2.5-7b-instruct"
        if not explicit_temp:
            temp = 0.5
    elif preset == "balanced":
        if not explicit_model:
            model = "mistralai/mistral-nemo"
        if not explicit_temp:
            temp = 0.6
    elif preset == "quality":
        if not explicit_model:
            model = "qwen/qwen-2.5-14b-instruct"
        if not explicit_temp:
            temp = 0.7
    return settings.model_copy(update={"post_model": model, "post_temperature": temp})
