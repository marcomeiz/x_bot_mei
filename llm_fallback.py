import os
import json
from typing import Any, Dict, List, Optional

from openai import OpenAI
from dotenv import load_dotenv

from logger_config import logger
from src.settings import AppSettings


load_dotenv()

DEFAULT_TIMEOUT_SECONDS = float(os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "15") or 15.0)


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
        if not s.openrouter_api_key:
            logger.warning("OPENROUTER_API_KEY no configurada.")
        self.client = OpenAI(base_url=s.openrouter_base_url, api_key=s.openrouter_api_key)

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
        request_timeout = float(timeout) if timeout is not None else DEFAULT_TIMEOUT_SECONDS
        kwargs["timeout"] = request_timeout
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
                    "timeout": request_timeout,
                }
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
