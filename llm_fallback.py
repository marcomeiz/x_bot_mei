import os
import json
from typing import Any, Dict, List, Optional

from openai import OpenAI
from dotenv import load_dotenv

from logger_config import logger
from src.settings import AppSettings


load_dotenv()

# Timeout configurable vía env; si no se define o es <=0, no se aplica.
_raw_timeout = os.getenv("LLM_REQUEST_TIMEOUT_SECONDS")
try:
    DEFAULT_TIMEOUT_SECONDS = float(_raw_timeout) if _raw_timeout is not None else None
    if DEFAULT_TIMEOUT_SECONDS is not None and DEFAULT_TIMEOUT_SECONDS <= 0:
        DEFAULT_TIMEOUT_SECONDS = None
except Exception:
    DEFAULT_TIMEOUT_SECONDS = None


# Pricing por 1M tokens (OpenRouter, aproximados)
MODEL_PRICING = {
    "google/gemini-2.5-pro": {"input": 0.001875, "output": 0.015},
    "anthropic/claude-opus-4.1": {"input": 0.015, "output": 0.075},
    "anthropic/claude-3.5-sonnet": {"input": 0.003, "output": 0.015},
    "deepseek/deepseek-chat-v3.1": {"input": 0.00014, "output": 0.00028},
    # Fallback genérico
    "_default": {"input": 0.001, "output": 0.002},
}


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estima el costo de una llamada LLM basado en los tokens."""
    pricing = MODEL_PRICING.get(model, MODEL_PRICING["_default"])
    input_cost = (input_tokens / 1_000_000) * pricing["input"]
    output_cost = (output_tokens / 1_000_000) * pricing["output"]
    return input_cost + output_cost


def _parse_json_robust(text: str) -> Any:
    # Log raw response for debugging Gemini verbosity
    logger.info(f"[LLM_RESPONSE_RAW] Length: {len(text)} chars, First 500 chars: {text[:500]}")
    logger.info(f"[LLM_RESPONSE_RAW] Last 500 chars: {text[-500:]}")

    try:
        return json.loads(text)
    except Exception:
        pass
    lo = text.find("{")
    ro = text.rfind("}")
    la = text.find("[")
    ra = text.rfind("]")
    for a, b in ((lo, ro), (la, ra)):
        if a != -1 and b != -1 and b > a:
            try:
                return json.loads(text[a : b + 1])
            except Exception:
                continue

    # Log full response if parsing fails
    logger.error(f"[LLM_RESPONSE_FULL] Could not parse JSON. Full response:\n{text}")
    raise ValueError("No valid JSON could be parsed from response")


class OpenRouterLLM:
    def __init__(self) -> None:
        s = AppSettings.load()
        api_key = (s.openrouter_api_key or "").strip()
        if not api_key:
            logger.warning("OPENROUTER_API_KEY no configurada.")
        self.client = OpenAI(base_url=s.openrouter_base_url, api_key=api_key)

    def _call(
        self,
        *,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float,
        json_mode: bool,
        timeout: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        request_timeout = timeout if timeout is not None else DEFAULT_TIMEOUT_SECONDS
        if request_timeout is not None:
            kwargs["timeout"] = float(request_timeout)

        try:
            resp = self.client.chat.completions.create(**kwargs)
            content = (resp.choices[0].message.content or "").strip()

            # Token usage logging (detectar sangría de tokens)
            if hasattr(resp, "usage") and resp.usage:
                input_tokens = resp.usage.prompt_tokens
                output_tokens = resp.usage.completion_tokens
                total_tokens = resp.usage.total_tokens
                cost = _estimate_cost(model, input_tokens, output_tokens)

                logger.info(
                    f"[TOKEN_USAGE] model={model} | "
                    f"input={input_tokens:,} | output={output_tokens:,} | total={total_tokens:,} | "
                    f"cost=${cost:.6f} | temp={temperature} | json_mode={json_mode}"
                )

                # Alerta si output tokens son excesivos (>2000 para cualquier llamada)
                if output_tokens > 2000:
                    logger.warning(
                        f"[TOKEN_BLEEDING] ⚠️ High output tokens detected! "
                        f"model={model} output={output_tokens:,} tokens (${cost:.6f})"
                    )

            return content

        except Exception as e:
            # Retry without response_format if provider rejects it
            msg = str(e).lower()
            if json_mode and ("response_format" in msg or "type" in msg or "unsupported" in msg):
                fallback_kwargs = {
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                }
                if request_timeout is not None:
                    fallback_kwargs["timeout"] = float(request_timeout)

                resp = self.client.chat.completions.create(**fallback_kwargs)
                content = (resp.choices[0].message.content or "").strip()

                # Token usage logging para fallback también
                if hasattr(resp, "usage") and resp.usage:
                    input_tokens = resp.usage.prompt_tokens
                    output_tokens = resp.usage.completion_tokens
                    total_tokens = resp.usage.total_tokens
                    cost = _estimate_cost(model, input_tokens, output_tokens)

                    logger.info(
                        f"[TOKEN_USAGE] (fallback) model={model} | "
                        f"input={input_tokens:,} | output={output_tokens:,} | total={total_tokens:,} | "
                        f"cost=${cost:.6f} | temp={temperature}"
                    )

                return content
            raise

    def chat_text(
        self,
        *,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        timeout: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        return self._call(model=model, messages=messages, temperature=temperature, json_mode=False, timeout=timeout, max_tokens=max_tokens)

    def chat_json(
        self,
        *,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.2,
        timeout: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Any:
        text = self._call(model=model, messages=messages, temperature=temperature, json_mode=True, timeout=timeout, max_tokens=max_tokens)
        return _parse_json_robust(text)


llm = OpenRouterLLM()
