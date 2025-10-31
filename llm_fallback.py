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


def _parse_json_robust(text: str) -> Any:
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
    ) -> str:
        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        request_timeout = timeout if timeout is not None else DEFAULT_TIMEOUT_SECONDS
        if request_timeout is not None:
            kwargs["timeout"] = float(request_timeout)
        try:
            resp = self.client.chat.completions.create(**kwargs)
            return (resp.choices[0].message.content or "").strip()
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
                return (resp.choices[0].message.content or "").strip()
            raise

    def chat_text(
        self,
        *,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        timeout: Optional[float] = None,
    ) -> str:
        return self._call(model=model, messages=messages, temperature=temperature, json_mode=False, timeout=timeout)

    def chat_json(
        self,
        *,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.2,
        timeout: Optional[float] = None,
    ) -> Any:
        text = self._call(model=model, messages=messages, temperature=temperature, json_mode=True, timeout=timeout)
        return _parse_json_robust(text)


llm = OpenRouterLLM()
