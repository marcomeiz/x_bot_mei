"""Core tweet generation orchestration.

This module keeps the public API stable (`generate_tweet_from_topic`,
`generate_third_tweet_variant`, `find_relevant_topic`, `find_topic_by_id`) while
delegating prompt construction and variant-specific logic to
`variant_generators.py`. The goal is to keep these functions lean and focused on
control-flow, logging and data access.
"""

import os
import random
from typing import Dict, Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from embeddings_manager import get_embedding, get_memory_collection, get_topics_collection
from logger_config import logger
from prompt_context import build_prompt_context
from style_guard import StyleRejection
from variant_generators import GenerationSettings, generate_variant_ab_pair, generate_variant_c


class TweetDrafts(BaseModel):
    draft_a: str = Field(..., description="The first tweet draft, labeled as A.")
    draft_b: str = Field(..., description="The second tweet draft, labeled as B.")


load_dotenv()

GENERATION_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-pro")
VALIDATION_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-pro")
SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", "0.20") or 0.20)
MAX_GENERATION_ATTEMPTS = 3


def _build_settings() -> GenerationSettings:
    return GenerationSettings(
        generation_model=GENERATION_MODEL,
        validation_model=VALIDATION_MODEL,
    )


def _flatten_maybe(nested):
    if isinstance(nested, list) and nested and isinstance(nested[0], list):
        return [item for sub in nested for item in sub]
    return nested


def _flatten_metadata(raw):
    if isinstance(raw, list) and raw and isinstance(raw[0], list):
        flat = []
        for sub in raw:
            flat.extend(sub)
        return flat
    return raw or []

def _extract_topic_entry(collection, topic_id: str) -> Optional[Dict[str, str]]:
    try:
        data = collection.get(ids=[topic_id], include=["documents", "metadatas"])  # type: ignore[arg-type]
    except Exception as exc:
        logger.warning("No se pudo recuperar el tema %s: %s", topic_id, exc)
        return None

    docs = data.get("documents") if isinstance(data, dict) else None
    if not docs:
        return None
    abstract = docs[0][0] if isinstance(docs[0], list) else docs[0]

    pdf_name = None
    metadatas = data.get("metadatas") if isinstance(data, dict) else None
    if metadatas:
        md_entry = metadatas[0][0] if isinstance(metadatas[0], list) and metadatas[0] else metadatas[0]
        if isinstance(md_entry, dict):
            pdf_name = md_entry.get("pdf") or md_entry.get("source_pdf")

    result = {"topic_id": topic_id, "abstract": abstract}
    if pdf_name:
        result["source_pdf"] = pdf_name
    return result


def _log_similarity(topic_abstract: str, ignore_similarity: bool) -> None:
    if ignore_similarity:
        logger.info("Saltando comprobación de similitud por 'ignore_similarity=True'.")
        return

    logger.info("Iniciando comprobación de similitud en memoria.")
    memory_collection = get_memory_collection()
    try:
        has_memory = memory_collection.count() > 0
    except Exception:
        has_memory = False

    if not has_memory:
        logger.info("Memoria vacía; sin comprobación de similitud.")
        return

    topic_embedding = get_embedding(topic_abstract)
    if topic_embedding is None:
        logger.info("Embedding del tema no disponible; se omite comprobación de similitud.")
        return

    try:
        results = memory_collection.query(query_embeddings=[topic_embedding], n_results=1)
        distance = results and results["distances"][0][0]
        similar_id = results and results["ids"][0][0]
    except Exception:
        distance = None
        similar_id = None

    if isinstance(distance, (int, float)) and distance < SIMILARITY_THRESHOLD:
        logger.warning(
            "Similitud detectada. Distancia: %.4f (Umbral: %.2f). Tuit similar ID: %s.",
            distance,
            SIMILARITY_THRESHOLD,
            similar_id,
        )
    logger.info("Comprobación de similitud finalizada.")


def generate_tweet_from_topic(topic_abstract: str, ignore_similarity: bool = True):
    context = build_prompt_context()
    settings = _build_settings()

    _log_similarity(topic_abstract, ignore_similarity)

    last_style_feedback = ""
    for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
        logger.info("Intento de generación de IA %s/%s…", attempt, MAX_GENERATION_ATTEMPTS)
        try:
            draft_a, draft_b = generate_variant_ab_pair(topic_abstract, context, settings)
            logger.info("Intento %s: borradores A/B generados y validados.", attempt)
            return draft_a, draft_b
        except StyleRejection as rejection:
            last_style_feedback = str(rejection).strip()
            logger.warning("Rechazo del revisor final en intento %s: %s", attempt, last_style_feedback)
        except Exception as exc:
            logger.error("Error crítico en el intento %s: %s", attempt, exc, exc_info=True)

    logger.error("No se pudo generar un borrador válido tras varios intentos.")
    if last_style_feedback:
        return (
            f"Error: Style rejection tras {MAX_GENERATION_ATTEMPTS} intentos. Feedback: {last_style_feedback}",
            "",
        )
    return "Error: No se pudo generar un borrador válido tras varios intentos.", ""


def generate_third_tweet_variant(topic_abstract: str):
    context = build_prompt_context()
    settings = _build_settings()
    try:
        return generate_variant_c(topic_abstract, context, settings)
    except StyleRejection:
        # Se deja propagar para que el llamador maneje el feedback.
        raise
    except Exception as exc:
        logger.error("Error generating third variant: %s", exc, exc_info=True)
        return "", ""


def find_relevant_topic(sample_size: int = 5):
    """Devuelve un tema aleatorio priorizando baja similitud con la memoria."""

    logger.info("Buscando tema en 'topics_collection' (preferir menos similar a memoria)…")
    topics_collection = get_topics_collection()
    try:
        raw = topics_collection.get(include=["metadatas"])  # type: ignore[arg-type]
        all_ids = _flatten_maybe(raw.get("ids") if isinstance(raw, dict) else None)
        all_metadata = _flatten_metadata(raw.get("metadatas") if isinstance(raw, dict) else None)
        if not all_ids:
            logger.warning("'topics_collection' está vacía. No se pueden encontrar temas.")
            return None
        approved_ids = []
        if all_metadata and len(all_metadata) == len(all_ids):
            for cid, meta in zip(all_ids, all_metadata):
                if isinstance(meta, dict) and meta.get("status") == "approved":
                    approved_ids.append(cid)

        pool = list(approved_ids or all_ids)
        if approved_ids:
            logger.info("Se encontraron %s temas aprobados. Se priorizarán sobre %s totales.", len(approved_ids), len(all_ids))
        else:
            logger.info("No hay temas aprobados; se usará el total disponible.")

        candidates = random.sample(pool, min(sample_size, len(pool)))

        memory_collection = get_memory_collection()
        try:
            has_memory = memory_collection.count() > 0
        except Exception:
            has_memory = False

        best_topic = None
        best_distance = -1.0

        for cid in candidates:
            entry = _extract_topic_entry(topics_collection, cid)
            if not entry:
                continue

            abstract = entry["abstract"]
            distance = 1.0
            if has_memory:
                embedding = get_embedding(abstract)
                if embedding is not None:
                    try:
                        res = memory_collection.query(query_embeddings=[embedding], n_results=1)
                        dist_val = res and res.get("distances") and res["distances"][0][0]
                        distance = float(dist_val) if isinstance(dist_val, (int, float)) else 0.0
                    except Exception:
                        distance = 0.0

            if distance > best_distance:
                best_distance = distance
                best_topic = entry

        if best_topic:
            logger.info(
                "Tema seleccionado (menos similar en muestra %s), distancia≈%.4f",
                len(candidates),
                best_distance,
            )
            return best_topic

        # Fallback absoluto: devolver cualquier tema
        fallback_id = random.choice(all_ids)
        return _extract_topic_entry(topics_collection, fallback_id)
    except Exception as exc:
        logger.error("Error al buscar un tema en ChromaDB: %s", exc, exc_info=True)
    return None


def find_topic_by_id(topic_id: str):
    """Recupera un tema específico desde ChromaDB."""

    logger.info("Buscando tema específico por ID: %s", topic_id)
    topics_collection = get_topics_collection()
    try:
        entry = _extract_topic_entry(topics_collection, topic_id)
        if entry:
            logger.info("Tema con ID %s encontrado en ChromaDB.", topic_id)
            return entry
    except Exception as exc:
        logger.error("Error al buscar tema por ID %s en ChromaDB: %s", topic_id, exc, exc_info=True)

    logger.warning("Tema con ID %s no encontrado en ChromaDB.", topic_id)
    return None
