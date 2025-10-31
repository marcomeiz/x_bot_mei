import copy
import os
from functools import lru_cache
from pathlib import Path
from typing import Dict, Any

import yaml

from logger_config import logger


DEFAULT_MESSAGES: Dict[str, Any] = {
    "job_timeout": "⌛ Estoy al máximo ahora mismo. Intenta nuevamente en unos segundos.",
    "queue_busy": "⚠️ Ya estoy trabajando en tu última solicitud. Dame unos segundos.",
    "queue_full": "⌛ Estoy al máximo ahora mismo. Intenta en breve.",
    "generate_error": "❌ Ocurrió un error inesperado al generar la propuesta.",
    "no_topics_available": "❌ No encontré temas disponibles para generar en este momento.",
    "proposal_generation_failed": "❌ No pude generar propuesta para este tema. Inténtalo nuevamente en unos minutos.",
    "comment_missing_text": "Necesito que pegues el texto de la publicación después de /comentar.",
    "comment_preparing": "💬 Preparando respuesta…",
    "comment_post_snippet": "🗞️ Post: {snippet}",
    "comment_skip_default_reason": "Sin ángulo claro para aportar valor.",
    "comment_skip": "🙅‍♂️ Mejor no comentar: {reason}",
    "comment_style_rejected": "⚠️ El validador externo rechazó el comentario. Ajusta el texto o intenta de nuevo.",
    "comment_error": "❌ No pude generar un comentario ahora mismo. Inténtalo nuevamente en unos minutos.",
    "selecting_topic": "🧠 Seleccionando tema…",
    "topic_label": "✍️ Tema: {abstract}",
    "topic_origin": "📄 Origen: {source}",
    "generating_variants": "Generando 3 alternativas de longitud variable…",
    "provider_error": "❌ Error del proveedor LLM: {reason}",
    "unexpected_error": "❌ Ocurrió un error inesperado: {error}",
    "variants_similar_initial": "⚠️ Variantes muy similares. Buscando otros ángulos…",
    "variants_similar_after": "⚠️ Variantes todavía similares. Intento de nuevo…",
    "variant_failure_header": "❌ No pude generar variantes publicables para este tema.",
    "variant_error_default": "No se generaron variantes utilizables.",
    "operation_cancelled": "Operación cancelada.",
    "draft_not_found": "⚠️ No pude localizar el borrador aprobado.",
    "draft_storage_missing": "⚠️ No pude recuperar el borrador aprobado (quizá expiró). Genera uno nuevo con el botón.",
    "option_not_available": "⚠️ La opción elegida no está disponible.",
    "similarity_warning": (
        "⚠️ El borrador elegido parece muy similar a una publicación previa.\n"
        "Distancia: {distance:.4f} (umbral {threshold}).\n"
        "¿Confirmas guardarlo igualmente?"
    ),
    "confirm_failure": "⚠️ No pude completar la confirmación. Genera uno nuevo.",
    "manual_confirmation_prefix": "Guardado pese a similitud.",
    "publish_prompt": "Usa el siguiente botón para publicar:",
    "memory_added": "✅ Añadido a la memoria. Ya hay {total} publicaciones.",
    "ready_for_next": "Listo para el siguiente.",
    "contract_retry": "⚠️ Variantes no cumplen el contrato. Reintentando…",
    "contract_failure": "❌ No pude generar variantes que cumplan el contrato. Intenta de nuevo más tarde.",
    "chroma_missing": "❌ CHROMA_DB_URL no está configurada.",
    "ping_success": "✅ Ping exitoso. Respuesta: {response}",
    "ping_failure": "❌ Ping fallido: {error}",
    "unknown_command": "Comando no reconocido. Usa /generate para obtener propuestas.",
    "db_query_error": "❌ No pude consultar la base de datos.",
    "variant_missing_note": "No se pudo generar esta variante.",
}


def _default_messages_path() -> Path:
    override = os.getenv("MESSAGES_CONFIG_PATH")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / "config" / "messages.yaml"


@lru_cache(maxsize=1)
def _load_messages() -> Dict[str, Any]:
    messages = copy.deepcopy(DEFAULT_MESSAGES)
    path = _default_messages_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        if not isinstance(data, dict):
            raise ValueError("Root YAML must be a mapping.")
        payload = data.get("messages") if "messages" in data else data
        if isinstance(payload, dict):
            for key, value in payload.items():
                if isinstance(value, (str, int, float)):
                    messages[key] = str(value)
                else:
                    logger.warning("Message '%s' in %s must be a string. Ignoring custom value.", key, path)
        else:
            logger.warning("Messages config at %s must map keys to strings; ignoring block.", path)
    except FileNotFoundError:
        logger.info("Messages config not found at %s. Using defaults.", path)
    except Exception as exc:
        logger.warning("Failed to load messages config from %s: %s. Using defaults.", path, exc)
    return messages


def get_message(key: str, **kwargs) -> str:
    messages = _load_messages()
    if key not in messages:
        raise KeyError(f"Message '{key}' not found in configuration.")
    template = messages[key]
    if kwargs:
        return template.format(**kwargs)
    return template
