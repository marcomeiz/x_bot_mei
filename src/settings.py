import os
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field


class AppSettings(BaseModel):
    # LLM provider (OpenRouter-only)
    openrouter_api_key: Optional[str] = Field(default=os.getenv("OPENROUTER_API_KEY"))
    openrouter_base_url: str = Field(default=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"))

    # Per-purpose model choices (cheap by default)
    post_model: str = Field(default=os.getenv("POST_MODEL", "qwen/qwen-2.5-7b-instruct"))
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

    @classmethod
    def load(cls) -> "AppSettings":
        path = os.getenv("APP_CONFIG_PATH")
        base = cls()
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
            }
            merged = base.model_dump()
            for k, v in data.items():
                env_name = env_map.get(k)
                env_set = bool(env_name and os.getenv(env_name))
                if not env_set:
                    merged[k] = v
            return cls(**merged)
        except Exception:
            return base
