import os
from functools import lru_cache

from dotenv import load_dotenv

from logger_config import logger


BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DEFAULT_CONTRACT_PATH = os.path.join(BASE_DIR, "copywriter_contract_hormozi.md")
DEFAULT_ICP_PATH = os.path.join(BASE_DIR, "config", "icp.md")

FALLBACK_CONTRACT_TEXT = (
    "Imperative tone. One sentence per paragraph. 5-12 words per sentence. "
    "Short, brutal, no hedging. Confront excuses. Focus on sacrifice, action, "
    "consistency, price of winning. Use simple vocabulary and strong verbs."
)
FALLBACK_ICP_TEXT = (
    "ICP: Solo-founders in day 1–year 1, overwhelmed by ops; want step-zero, practical tools. "
    "Platform: fast, conversational."
)

load_dotenv()


def _read_text_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as file:
        return file.read().strip()


def _resolve_path(env_var: str, default_path: str) -> str:
    override = os.getenv(env_var)
    if override:
        return override
    return default_path


@lru_cache(maxsize=1)
def get_style_contract_text() -> str:
    """Return the creative contract that defines the writer's voice."""
    path = _resolve_path("STYLE_CONTRACT_PATH", DEFAULT_CONTRACT_PATH)
    try:
        text = _read_text_file(path)
        logger.info(f"Contrato de copywriter cargado correctamente desde '{path}'.")
        return text
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.warning(
            f"No se pudo leer contrato creativo ('{path}'). Usando instrucciones mínimas. Error: {exc}"
        )
        return FALLBACK_CONTRACT_TEXT


@lru_cache(maxsize=1)
def get_icp_text() -> str:
    """Return the ICP (Ideal Customer Profile) description."""
    path = _resolve_path("ICP_PATH", DEFAULT_ICP_PATH)
    try:
        text = _read_text_file(path)
        logger.info(f"ICP cargado correctamente desde '{path}'.")
        return text
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.warning(
            f"No se pudo leer ICP ('{path}'). Usando ICP mínimo. Error: {exc}"
        )
        return FALLBACK_ICP_TEXT
