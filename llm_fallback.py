import os
import json
from typing import Any, Dict, List, Optional

from openai import OpenAI
from dotenv import load_dotenv

from logger_config import logger
from src.settings import AppSettings


load_dotenv()


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

    def _call(self, *, model: str, messages: List[Dict[str, str]], temperature: float, json_mode: bool) -> str:
        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            resp = self.client.chat.completions.create(**kwargs)
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:
            # Retry without response_format if provider rejects it
            msg = str(e).lower()
            if json_mode and ("response_format" in msg or "type" in msg or "unsupported" in msg):
                resp = self.client.chat.completions.create(model=model, messages=messages, temperature=temperature)
                return (resp.choices[0].message.content or "").strip()
            raise

    def chat_text(self, *, model: str, messages: List[Dict[str, str]], temperature: float = 0.7) -> str:
        return self._call(model=model, messages=messages, temperature=temperature, json_mode=False)

    def chat_json(self, *, model: str, messages: List[Dict[str, str]], temperature: float = 0.2) -> Any:
        text = self._call(model=model, messages=messages, temperature=temperature, json_mode=True)
        return _parse_json_robust(text)


llm = OpenRouterLLM()
