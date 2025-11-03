import os
import json
try:
    import instructor  # optional; only needed for structured outputs
    _INSTRUCTOR_AVAILABLE = True
except ImportError:
    instructor = None  # type: ignore
    _INSTRUCTOR_AVAILABLE = False
from pydantic import BaseModel
from typing import Any, Dict, List, Optional, Union, Type


from dotenv import load_dotenv

# Proveedores
from openai import OpenAI  # OpenRouter vía API OpenAI
# Eliminado: dependencia de Gemini. Solo OpenRouter.

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
        # Fuerza proveedor único OpenRouter independientemente del entorno
        self.provider_order = ["openrouter"]

        # OpenRouter
        self._openrouter_api_key = os.getenv("OPENROUTER_API_KEY")
        self._openrouter_client: Optional[OpenAI] = None

    # --- Inicializadores perezosos ---
    def _ensure_openrouter(self) -> bool:
        if not self._openrouter_api_key:
            logger.warning("OPENROUTER_API_KEY no configurada; saltando OpenRouter.")
            return False
        if self._openrouter_client is None:
            self._openrouter_client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=self._openrouter_api_key)
            if _INSTRUCTOR_AVAILABLE:
                try:
                    self._openrouter_client = instructor.patch(self._openrouter_client, mode=instructor.Mode.TOOLS)
                    logger.info("Instructor patch applied to OpenRouter client.")
                except Exception as exc:
                    logger.warning(f"No se pudo aplicar patch de instructor: {exc}")
        return True

    # --- Proveedores ---
    def _create_chat_completion(self, client: OpenAI, model: str, messages: List[Dict[str, str]], temperature: float, json_mode: bool) -> str:
        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        response = client.chat.completions.create(**kwargs)
        return response.choices[0].message.content.strip()


    # --- API pública ---
    def chat_text(self, *, model: str, messages: List[Dict[str, str]], temperature: float = 0.7) -> str:
        last_err: Optional[Exception] = None
        for provider in self.provider_order:
            if provider == "openrouter" and self._ensure_openrouter():
                try:
                    logger.info(f"LLM provider: OpenRouter ({model})")
                    return self._create_chat_completion(self._openrouter_client, model, messages, temperature, False)
                except Exception as e:
                    last_err = e
                    logger.error(f"Error no recuperable en OpenRouter: {e}")
                    raise

        if last_err:
            raise last_err
        raise RuntimeError("No hay proveedor LLM disponible")

    def chat_json(self, *, model: str, messages: List[Dict[str, str]], temperature: float = 0.2) -> Any:
        last_err: Optional[Exception] = None
        for provider in self.provider_order:
            if provider == "openrouter" and self._ensure_openrouter():
                try:
                    logger.info(f"LLM provider (JSON): OpenRouter ({model})")
                    text = self._create_chat_completion(self._openrouter_client, model, messages, temperature, True)
                    return _parse_json_robust(text)
                except Exception as e:
                    last_err = e
                    logger.error(f"Error no recuperable en OpenRouter (JSON): {e}")
                    raise

        if last_err:
            raise last_err
        raise RuntimeError("No hay proveedor LLM disponible para salida JSON")

    def chat_structured(self, *, model: str, messages: List[Dict[str, str]], response_model: Type[BaseModel], temperature: float = 0.7) -> BaseModel:
        if not _INSTRUCTOR_AVAILABLE:
            raise RuntimeError("Structured outputs no disponibles: instala 'instructor' si necesitas esta funcionalidad.")
        last_err: Optional[Exception] = None
        for provider in self.provider_order:
            client: Optional[OpenAI] = None
            provider_model_name = model
            
            if provider == "openrouter" and self._ensure_openrouter():
                client = self._openrouter_client
                logger.info(f"LLM provider (Structured): OpenRouter ({model})")

            if client:
                try:
                    return client.chat.completions.create(
                        model=provider_model_name,
                        messages=messages,
                        temperature=temperature,
                        response_model=response_model,
                    )
                except Exception as e:
                    last_err = e
                    logger.error(f"Error no recuperable en OpenRouter (Structured): {e}")
                    raise

        # Sin fallback adicional: solo OpenRouter.

        if last_err:
            raise last_err
        raise RuntimeError("No hay proveedor LLM disponible para salida estructurada")



# Instancia lista para importar
llm = LLMFallback()
