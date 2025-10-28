"""Core tweet generation orchestration.

This module keeps the public API stable (`generate_tweet_from_topic`,
`generate_third_tweet_variant`, `find_relevant_topic`, `find_topic_by_id`) while
delegating prompt construction and variant-specific logic to
`variant_generators.py`. The goal is to keep these functions lean and focused on
control-flow, logging and data access.
"""

import os
import random
import time
from typing import Dict, Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from embeddings_manager import get_embedding, get_memory_collection, get_topics_collection, find_similar_topics
from logger_config import logger
from prompt_context import build_prompt_context
from style_guard import StyleRejection, audit_and_improve_comment
from variant_generators import (
    GenerationSettings,
    CommentResult,
    CommentAssessment,
    generate_all_variants,
    assess_comment_opportunity,
    generate_comment_reply,
)


class TweetDrafts(BaseModel):
    draft_a: str = Field(..., description="The first tweet draft, labeled as A.")
    draft_b: str = Field(..., description="The second tweet draft, labeled as B.")


class CommentDraft(BaseModel):
    comment: str = Field(..., description="Single comment ready to reply with.")
    insight: Optional[str] = Field(default=None, description="Short note about the angle used.")
    metadata: Dict[str, object] = Field(default_factory=dict)


class CommentSkip(Exception):
    """Raised when the model decides the post is not worth commenting."""

    def __init__(self, message: str = "") -> None:
        super().__init__(message)
        self.message = message


load_dotenv()

from src.settings import AppSettings
settings = AppSettings.load()
GENERATION_MODEL = settings.post_model
VALIDATION_MODEL = settings.post_model
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


def generate_tweet_from_topic(topic_abstract: str, ignore_similarity: bool = True) -> Dict[str, str]:
    context = build_prompt_context()
    settings = _build_settings()

    _log_similarity(topic_abstract, ignore_similarity)

    last_error = ""
    for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
        logger.info("Intento de generación de IA %s/%s…", attempt, MAX_GENERATION_ATTEMPTS)
        try:
            drafts = generate_all_variants(topic_abstract, context, settings)
            logger.info("Intento %s: todos los borradores generados y validados.", attempt)
            return drafts
        except StyleRejection as rejection:
            last_error = str(rejection).strip()
            logger.warning("Rechazo del revisor final en intento %s: %s", attempt, last_error)
        except Exception as exc:
            last_error = str(exc)
            logger.error("Error crítico en el intento %s: %s", attempt, exc, exc_info=True)

    logger.error("No se pudo generar un borrador válido tras varios intentos.")
    error_message = f"Error: {last_error}" if last_error else "Error: No se pudo generar un borrador válido."
    return {"error": error_message}


def generate_comment_from_text(source_text: str) -> CommentDraft:
    """Generate a conversational comment responding to arbitrary source text."""
    context = build_prompt_context()
    settings = _build_settings()
    last_style_feedback = ""
    last_error = ""

    assessment: CommentAssessment = assess_comment_opportunity(source_text, context, settings)
    if not assessment.should_comment:
        reason = assessment.reason or "No hay valor claro para aportar."
        logger.info("Comentario omitido por evaluación previa: %s", reason)
        raise CommentSkip(reason)

    for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
        logger.info("Intento %s/%s de generar comentario para interacción.", attempt, MAX_GENERATION_ATTEMPTS)
        try:
            result: CommentResult = generate_comment_reply(
                source_text, context, settings, assessment=assessment
            )

            # --- v4.0 Guardian Layer ---
            audited_comment, was_rewritten = audit_and_improve_comment(
                result.comment, source_text, context.contract
            )
            result.comment = audited_comment
            if was_rewritten:
                result.metadata["rewritten_by_guardian"] = True
            # --- End Guardian Layer ---

            metadata = dict(result.metadata)
            metadata.setdefault("assessment_reason", assessment.reason)
            if assessment.hook and "assessment_hook" not in metadata:
                metadata["assessment_hook"] = assessment.hook
            if assessment.risk and "assessment_risk" not in metadata:
                metadata["assessment_risk"] = assessment.risk
            return CommentDraft(comment=result.comment, insight=result.insight, metadata=metadata)
        except StyleRejection as rejection:
            last_style_feedback = str(rejection).strip()
            logger.warning("Rechazo de estilo en comentario (intento %s): %s", attempt, last_style_feedback)
        except Exception as exc:
            last_error = str(exc)
            logger.error("Error generando comentario en intento %s: %s", attempt, exc, exc_info=True)

    if last_style_feedback:
        raise StyleRejection(f"Falló la auditoría de estilo al generar comentario: {last_style_feedback}")
    raise RuntimeError(
        "No se pudo generar un comentario válido."
        + (f" Último error: {last_error}" if last_error else "")
    )


def find_relevant_topic(sample_size: int = 3):
    start_time = time.time()

    logger.info("Buscando tema en 'topics_collection' (preferir menos similar a memoria)…")
    topics_collection = get_topics_collection()
    try:
        # 1) Fetch a small window of approved topics (fast path)
        raw = topics_collection.get(where={"status": {"$eq": "approved"}}, include=["metadatas", "documents"], limit=200)  # type: ignore[arg-type]
        ids_approved = _flatten_maybe(raw.get("ids") if isinstance(raw, dict) else None)
        # 2) If none approved, fetch a random window from all
        if not ids_approved:
            try:
                total = topics_collection.count()  # type: ignore
            except Exception:
                total = None
            offset = 0
            if isinstance(total, int) and total > 200:
                offset = random.randrange(0, max(total - 200, 1))
            raw = topics_collection.get(include=["metadatas", "documents"], limit=200, offset=offset)  # type: ignore[arg-type]
            all_ids = _flatten_maybe(raw.get("ids") if isinstance(raw, dict) else None)
            pool = list(all_ids or [])
        else:
            pool = list(ids_approved)

        if not pool:
            logger.warning("'topics_collection' no devolvió IDs. No se pueden encontrar temas.")
            return None

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
            logger.info(f"[PERF] find_relevant_topic took {time.time() - start_time:.2f} seconds.")
            return best_topic

        # Fallback absoluto: devolver cualquier tema
        fallback_id = random.choice(all_ids)
        logger.info(f"[PERF] find_relevant_topic (fallback) took {time.time() - start_time:.2f} seconds.")
        return _extract_topic_entry(topics_collection, fallback_id)
    except Exception as exc:
        logger.error("Error al buscar un tema en ChromaDB: %s", exc, exc_info=True)
    logger.info(f"[PERF] find_relevant_topic (error path) took {time.time() - start_time:.2f} seconds.")
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
