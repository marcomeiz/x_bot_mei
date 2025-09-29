import os
import json
from typing import Any, Dict, List, Optional, Union

from dotenv import load_dotenv

# Proveedores
from openai import OpenAI  # OpenRouter vía API OpenAI
import google.generativeai as genai  # Gemini

from logger_config import logger


load_dotenv()


def _messages_to_prompt(messages: List[Dict[str, str]]) -> str:
    parts = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "system":
            parts.append(f"System: {content}")
        elif role == "assistant":
            parts.append(f"Assistant: {content}")
        else:
            parts.append(f"User: {content}")
    return "\n\n".join(parts).strip()


def _should_fallback_for_error(err: Exception) -> bool:
    text = (repr(err) + " " + str(getattr(err, "message", ""))).lower()
    triggers = [
        "insufficient", "quota", "credit", "payment", "billing",
        "402", "429", "rate limit", "unauthorized", "401", "403",
    ]
    return any(t in text for t in triggers)


def _parse_json_robust(text: str) -> Any:
    # Intento directo
    try:
        return json.loads(text)
    except Exception:
        pass

    # Extraer primer bloque JSON entre [] o {}
    first_obj = text.find("{")
    last_obj = text.rfind("}")
    first_arr = text.find("[")
    last_arr = text.rfind("]")

    candidates = []
    if first_obj != -1 and last_obj != -1 and last_obj > first_obj:
        candidates.append(text[first_obj:last_obj + 1])
    if first_arr != -1 and last_arr != -1 and last_arr > first_arr:
        candidates.append(text[first_arr:last_arr + 1])

    for c in candidates:
        try:
            return json.loads(c)
        except Exception:
            continue

    raise ValueError("No valid JSON could be parsed from response")


class LLMFallback:
    def __init__(self) -> None:
        self.provider_order = [p.strip() for p in os.getenv("FALLBACK_PROVIDER_ORDER", "openrouter,gemini").split(",") if p.strip()]

        # OpenRouter
        self._openrouter_api_key = os.getenv("OPENROUTER_API_KEY")
        self._openrouter_client: Optional[OpenAI] = None

        # Gemini
        self._google_api_key = os.getenv("GOOGLE_API_KEY")
        self._gemini_model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-pro")
        self._gemini_model: Optional[Any] = None

    # --- Inicializadores perezosos ---
    def _ensure_openrouter(self) -> bool:
        if not self._openrouter_api_key:
            logger.warning("OPENROUTER_API_KEY no configurada; saltando OpenRouter.")
            return False
        if self._openrouter_client is None:
            self._openrouter_client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=self._openrouter_api_key)
        return True

    def _ensure_gemini(self) -> bool:
        if not self._google_api_key:
            logger.warning("GOOGLE_API_KEY no configurada; no se puede usar Gemini.")
            return False
        try:
            genai.configure(api_key=self._google_api_key)
            if self._gemini_model is None:
                self._gemini_model = genai.GenerativeModel(self._gemini_model_name)
            return True
        except Exception as e:
            logger.error(f"Error configurando Gemini: {e}")
            return False

    # --- Proveedores ---
    def _openrouter_chat(self, *, model: str, messages: List[Dict[str, str]], temperature: float, json_mode: bool) -> str:
        assert self._openrouter_client is not None
        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        response = self._openrouter_client.chat.completions.create(**kwargs)
        return response.choices[0].message.content.strip()

    def _gemini_chat(self, *, messages: List[Dict[str, str]], temperature: float, json_mode: bool) -> str:
        assert self._gemini_model is not None
        prompt = _messages_to_prompt(messages)

        if json_mode:
            prompt = (
                prompt
                + "\n\nYou must respond ONLY with strict JSON. No prose, no markdown."
            )

        gen_cfg: Dict[str, Any] = {"temperature": temperature}
        if json_mode:
            gen_cfg["response_mime_type"] = "application/json"

        # Intento con el modelo actual; si falla por modelo no soportado, probar alternativos
        try:
            resp = self._gemini_model.generate_content(prompt, generation_config=gen_cfg)
            return (getattr(resp, "text", None) or "").strip()
        except Exception as e:
            msg = str(e).lower()
            needs_alt = any(t in msg for t in ["not found", "is not supported for generatecontent", "404", "listmodels"])
            if not needs_alt:
                raise
            # Probar lista de modelos alternativos según versiones del SDK
            alt_names = [
                "gemini-2.5-pro",
                "gemini-2.5-flash",
                "gemini-2.0-flash",
                "gemini-2.0-pro-exp",
                "gemini-2.0-flash-lite",
                "gemini-1.5-pro-latest",
                "gemini-pro",
                "gemini-1.0-pro",
                "gemini-1.5-flash",
                "gemini-1.5-flash-latest",
            ]
            for name in alt_names:
                try:
                    logger.warning(f"Modelo Gemini '{self._gemini_model_name}' no disponible; probando alternativo '{name}'.")
                    self._gemini_model = genai.GenerativeModel(name)
                    self._gemini_model_name = name
                    resp = self._gemini_model.generate_content(prompt, generation_config=gen_cfg)
                    return (getattr(resp, "text", None) or "").strip()
                except Exception:
                    continue
            # Si ninguno funcionó, relanzar el error original
            raise e

    # --- API pública ---
    def chat_text(self, *, model: str, messages: List[Dict[str, str]], temperature: float = 0.7) -> str:
        last_err: Optional[Exception] = None
        for provider in self.provider_order:
            if provider == "openrouter" and self._ensure_openrouter():
                try:
                    logger.info(f"LLM provider: OpenRouter ({model})")
                    return self._openrouter_chat(model=model, messages=messages, temperature=temperature, json_mode=False)
                except Exception as e:
                    last_err = e
                    if _should_fallback_for_error(e):
                        logger.warning(f"Falló OpenRouter, aplicando fallback a Gemini: {e}")
                        # continuar a siguiente proveedor
                    else:
                        logger.error(f"Error no recuperable en OpenRouter: {e}")
                        raise

            if provider == "gemini" and self._ensure_gemini():
                logger.info(f"LLM provider: Gemini ({self._gemini_model_name})")
                return self._gemini_chat(messages=messages, temperature=temperature, json_mode=False)

        # Si llegamos aquí, no hay proveedor utilizable
        if last_err:
            raise last_err
        raise RuntimeError("No hay proveedor LLM disponible (OpenRouter/Gemini)")

    def chat_json(self, *, model: str, messages: List[Dict[str, str]], temperature: float = 0.2) -> Any:
        last_err: Optional[Exception] = None
        for provider in self.provider_order:
            if provider == "openrouter" and self._ensure_openrouter():
                try:
                    logger.info(f"LLM provider (JSON): OpenRouter ({model})")
                    text = self._openrouter_chat(model=model, messages=messages, temperature=temperature, json_mode=True)
                    return _parse_json_robust(text)
                except Exception as e:
                    last_err = e
                    if _should_fallback_for_error(e):
                        logger.warning(f"Falló OpenRouter (JSON), fallback a Gemini: {e}")
                    else:
                        logger.error(f"Error no recuperable en OpenRouter (JSON): {e}")
                        raise

            if provider == "gemini" and self._ensure_gemini():
                logger.info(f"LLM provider (JSON): Gemini ({self._gemini_model_name})")
                text = self._gemini_chat(messages=messages, temperature=temperature, json_mode=True)
                return _parse_json_robust(text)

        if last_err:
            raise last_err
        raise RuntimeError("No hay proveedor LLM disponible para salida JSON")


# Instancia lista para importar
llm = LLMFallback()
