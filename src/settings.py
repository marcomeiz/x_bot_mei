import os
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field


class AppSettings(BaseModel):
    # LLM providers
    fallback_provider_order: str = Field(default=os.getenv("FALLBACK_PROVIDER_ORDER", "gemini,openrouter"))
    gemini_model: str = Field(default=os.getenv("GEMINI_MODEL", "gemini-2.5-pro"))
    openrouter_default_model: str = Field(default=os.getenv("OPENROUTER_DEFAULT_MODEL", "anthropic/claude-3.5-sonnet"))

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
                "fallback_provider_order": "FALLBACK_PROVIDER_ORDER",
                "gemini_model": "GEMINI_MODEL",
                "openrouter_default_model": "OPENROUTER_DEFAULT_MODEL",
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
