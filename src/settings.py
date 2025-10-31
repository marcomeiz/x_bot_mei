import os
from typing import Optional

import yaml
from pydantic import BaseModel, Field


class AppSettings(BaseModel):
    # LLM provider (OpenRouter-only)
    openrouter_api_key: Optional[str] = Field(default=os.getenv("OPENROUTER_API_KEY"))
    openrouter_base_url: str = Field(default=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"))

    # Per-purpose model choices (cheap by default)
    # Default generation model (can be overridden via POST_MODEL)
    post_model: str = Field(default=os.getenv("POST_MODEL", "x-ai/grok-4-fast"))
    post_refiner_model: str = Field(default=os.getenv("POST_REFINER_MODEL", os.getenv("POST_MODEL", "x-ai/grok-4-fast")))
    # Temperature for generation (allow override via POST_TEMPERATURE or POST_PRESET)
    post_temperature: float = Field(default=float(os.getenv("POST_TEMPERATURE", "0.6") or 0.6))
    # Optional preset to coordinate model + temperature: speed | balanced | quality
    post_preset: Optional[str] = Field(default=os.getenv("POST_PRESET"))
    eval_fast_model: str = Field(default=os.getenv("EVAL_FAST_MODEL", "qwen/qwen-2.5-7b-instruct"))
    eval_slow_model: str = Field(default=os.getenv("EVAL_SLOW_MODEL", "mistralai/mistral-nemo"))
    comment_audit_model: str = Field(default=os.getenv("COMMENT_AUDIT_MODEL", "qwen/qwen-2.5-7b-instruct"))
    comment_rewrite_model: str = Field(default=os.getenv("COMMENT_REWRITE_MODEL", "mistralai/mistral-nemo"))
    topic_extraction_model: str = Field(default=os.getenv("TOPIC_EXTRACTION_MODEL", "mistralai/mistral-nemo"))
    # Use a stable, widely supported default embedding model
    embed_model: str = Field(default=os.getenv("EMBED_MODEL", "openai/text-embedding-3-small"))

    # Paths
    prompts_dir: str = Field(default=os.getenv("PROMPTS_DIR", "prompts"))
    config_path: Optional[str] = Field(default=os.getenv("APP_CONFIG_PATH"))

    # Feature flags
    log_prompts: bool = Field(default=os.getenv("LOG_PROMPTS", "0").lower() in {"1", "true", "yes"})
    log_prompts_full: bool = Field(default=os.getenv("LOG_PROMPTS_FULL", "0").lower() in {"1", "true", "yes"})
    log_provider_decisions: bool = Field(default=os.getenv("LOG_PROVIDER_DECISIONS", "0").lower() in {"1", "true", "yes"})

    @classmethod
    def load(cls) -> "AppSettings":
        path = os.getenv("APP_CONFIG_PATH")
        base = cls()
        # Apply preset mapping before loading file overrides
        base = _apply_post_preset(base)
        if not path:
            return base
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
            if not isinstance(data, dict):
                return base
            # Environment has precedence; config overrides defaults when no env var is set for that field
            env_map = {
                "openrouter_api_key": "OPENROUTER_API_KEY",
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
                env_name = env_map.get(k)
                env_set = bool(env_name and os.getenv(env_name))
                if not env_set:
                    merged[k] = v
            obj = cls(**merged)
            # Re-apply preset mapping after file load, unless explicit env overrides exist
            return _apply_post_preset(obj)
        except Exception:
            return base


def _apply_post_preset(settings: AppSettings) -> AppSettings:
    """If POST_PRESET is set, adjust model and temperature accordingly, unless POST_MODEL/POST_TEMPERATURE are explicitly provided.

    Presets:
    - speed:    model=qwen/qwen-2.5-7b-instruct, temperature=0.5
    - balanced: model=mistralai/mistral-nemo,      temperature=0.6
    - quality:  model=qwen/qwen-2.5-14b-instruct,  temperature=0.7
    """
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
