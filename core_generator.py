"""Core tweet generation orchestration.

This module keeps the public API stable (`generate_tweet_from_topic`,
`find_relevant_topic`, `find_topic_by_id`) while
delegating prompt construction and variant-specific logic to
`variant_generators.py`. The goal is to keep these functions lean and focused on
control-flow, logging and data access.
"""

import math
import os
import random
import time
from datetime import datetime, timezone
from typing import Dict, Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from embeddings_manager import get_embedding, get_memory_collection, get_topics_collection
from logger_config import logger
from prompt_context import build_prompt_context
from style_guard import audit_and_improve_comment
from variant_generators import (
    GenerationSettings,
    generate_all_variants,
    assess_comment_opportunity,
    generate_comment_reply,
    CommentResult,
    CommentAssessment,
)
from metrics import Timer
from src.goldset import GOLDSET_MIN_SIMILARITY, max_similarity_to_goldset
from src.chroma_utils import flatten_chroma_array, flatten_chroma_metadatas


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
from src.goldset import retrieve_goldset_examples
settings = AppSettings.load()
GENERATION_MODEL = settings.post_model
VALIDATION_MODEL = settings.post_model
SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", "0.20") or 0.20)
MAX_GENERATION_ATTEMPTS = 3

_PROVIDER_ERROR_MARKERS = (
    "not a valid model",
    "model not found",
    "no such model",
    "invalid model",
    "insufficient funds",
    "insufficient balance",
    "insufficient credit",
    "insufficient quota",
    "quota exceeded",
    "insufficient tokens",
    "payment required",
    "billing hard limit",
    "rate limit",
    "429",
    "403",
    "openrouter",
    "todos los proveedores fallaron",
    "all providers failed",
    "authentication",
    "api key",
)


def _is_provider_error_message(message: str) -> bool:
    if not message:
        return False
    lower = message.lower()
    return any(marker in lower for marker in _PROVIDER_ERROR_MARKERS)


def _build_settings() -> GenerationSettings:
    return GenerationSettings(
        generation_model=GENERATION_MODEL,
        validation_model=VALIDATION_MODEL,
        generation_temperature=settings.post_temperature,
    )


def _parse_metadata_timestamp(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            # Interpret large numbers as milliseconds
            ts = float(value)
            if ts > 1e12:
                ts /= 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (ValueError, OSError):
            return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except ValueError:
        try:
            return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None


# NOTE: Legacy functions from Sistema 3 (multi-criterio). Not used in Sistema 2 (lejanía máxima).
# Kept for potential future reversion.
# def _compute_recency_score(metadata: Optional[Dict[str, object]], now: Optional[datetime] = None) -> float:
#     ...
# def _compute_priority_boost(metadata: Optional[Dict[str, object]]) -> float:
#     ...

def _extract_topic_entry(collection, topic_id: str) -> Optional[Dict[str, object]]:
    try:
        data = collection.get(ids=[topic_id], include=["documents", "metadatas"])  # type: ignore[arg-type]
    except Exception as exc:
        logger.warning("No se pudo recuperar el tema %s: %s", topic_id, exc)
        return None

    docs_raw = data.get("documents") if isinstance(data, dict) else None
    docs = flatten_chroma_array(docs_raw)
    if not docs:
        return None
    first_doc = docs[0]
    if isinstance(first_doc, list):
        first_doc = first_doc[0] if first_doc else ""
    abstract = str(first_doc)

    pdf_name = None
    metadata_entry = None
    metadatas_raw = data.get("metadatas") if isinstance(data, dict) else None
    metadatas = flatten_chroma_metadatas(metadatas_raw)
    if metadatas:
        metadata_entry = metadatas[0]
        pdf_name = metadata_entry.get("pdf") or metadata_entry.get("source_pdf")

    result = {"topic_id": topic_id, "abstract": abstract}
    if pdf_name:
        result["source_pdf"] = pdf_name
    if metadata_entry:
        result["metadata"] = metadata_entry
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

    # Política /g: no generar embeddings en ruta interactiva; usar solo caché si existe
    topic_embedding = get_embedding(topic_abstract, generate_if_missing=False)
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


def generate_tweet_from_topic(topic_abstract: str, ignore_similarity: bool = True, model_override: Optional[str] = None) -> Dict[str, object]:
    """
    Generate 3 tweet variants using the simplified generator.

    This now uses simple_generator which follows ONLY the Elastic Voice Contract.
    No hardcoded rules, no multiple validation layers.
    """
    _log_similarity(topic_abstract, ignore_similarity)

    last_error = ""
    provider_error_message = ""

    for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
        logger.info("Intento de generación de IA %s/%s… Model: %s", attempt, MAX_GENERATION_ATTEMPTS, model_override or "default")
        try:
            # Use the simplified generator
            from simple_generator import generate_and_validate

            with Timer("g_llm_simple_generation", labels={"attempt": attempt}):
                generation = generate_and_validate(topic_abstract, model_override=model_override)

            # Extract valid variants
            variant_errors = {}
            result = {}

            for variant in [generation.short, generation.mid, generation.long]:
                if variant.valid:
                    result[variant.label] = variant.text
                else:
                    result[variant.label] = ""
                    variant_errors[variant.label] = variant.failure_reason or "Failed validation"

            # If we got at least one valid variant, return success
            if any(result.values()):
                logger.info("Intento %s: variantes generadas (%s válidas).",
                           attempt, len([v for v in result.values() if v]))
                if variant_errors:
                    result["variant_errors"] = variant_errors
                return result

            last_error = "; ".join(variant_errors.values()) or "Sin variantes válidas."
            logger.warning("Intento %s sin variantes publicables: %s", attempt, last_error)

            if _is_provider_error_message(last_error):
                provider_error_message = last_error
                break

        except Exception as exc:
            last_error = str(exc)
            logger.error("Error crítico en el intento %s: %s", attempt, exc, exc_info=True)
            if _is_provider_error_message(last_error):
                provider_error_message = last_error
                break

    logger.error("No se pudo generar un borrador válido tras varios intentos.")
    error_message = f"Error: {last_error}" if last_error else "Error: No se pudo generar un borrador válido."
    if provider_error_message:
        return {"error": provider_error_message, "provider_error": True}
    if _is_provider_error_message(error_message):
        return {"error": error_message, "provider_error": True}
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


def find_relevant_topic(sample_size: int = 5):
    """
    Sistema 2: Selección por LEJANÍA MÁXIMA.

    Devuelve un tema eligiendo el MÁS DIFERENTE del último tweet publicado.
    Si no hay memoria, elige uno aleatorio de la muestra.

    Este sistema es más simple y obliga a que el flujo de aprobación funcione correctamente.
    """
    start_time = time.time()
    logger.info("Buscando tema (Sistema 2: lejanía máxima) en 'topics_collection'…")

    topics_collection = get_topics_collection()
    try:
        # 1) Fetch topics (prefer approved)
        logger.info("[Sistema 2] Step 1: Fetching approved topics from ChromaDB...")
        raw = topics_collection.get(where={"status": {"$eq": "approved"}}, include=["metadatas", "documents"], limit=200)  # type: ignore[arg-type]
        ids_approved = flatten_chroma_array(raw.get("ids") if isinstance(raw, dict) else None)
        logger.info(f"[Sistema 2] Found {len(ids_approved) if ids_approved else 0} approved topics")

        # 2) If none approved, fetch random window
        if not ids_approved:
            logger.info("[Sistema 2] Step 2: No approved topics, fetching random window...")
            try:
                total = topics_collection.count()  # type: ignore
            except Exception:
                total = None
            offset = 0
            if isinstance(total, int) and total > 200:
                offset = random.randrange(0, max(total - 200, 1))
            raw = topics_collection.get(include=["metadatas", "documents"], limit=200, offset=offset)  # type: ignore[arg-type]
            all_ids = flatten_chroma_array(raw.get("ids") if isinstance(raw, dict) else None)
            pool = list(all_ids or [])
            logger.info(f"[Sistema 2] Fetched {len(pool)} topics from random window (offset={offset}, total={total})")
        else:
            pool = list(ids_approved)

        if not pool:
            logger.error("[Sistema 2] CRITICAL: topics_collection is EMPTY - no topics available in ChromaDB")
            return None

        # 3) Random sample
        candidates = random.sample(pool, min(sample_size, len(pool)))
        logger.info(f"[Sistema 2] Step 3: Selected {len(candidates)} random candidates from pool of {len(pool)}")

        # 4) Check if we have memory
        memory_collection = get_memory_collection()
        has_memory = False
        try:
            memory_count = memory_collection.count()
            has_memory = memory_count > 0
            logger.info(f"[Sistema 2] Step 4: Memory collection has {memory_count} entries (has_memory={has_memory})")
        except Exception as e:
            logger.warning(f"[Sistema 2] Could not check memory collection: {e}")
            has_memory = False

        # 5) Find the MOST DISTANT topic from last published tweet
        best_topic = None
        best_distance = -1.0

        for cid in candidates:
            entry = _extract_topic_entry(topics_collection, cid)
            if not entry:
                continue

            abstract = entry["abstract"]

            if has_memory:
                # Calculate distance to last published tweet
                topic_embedding = get_embedding(abstract, generate_if_missing=False)
                if topic_embedding is not None:
                    try:
                        res = memory_collection.query(query_embeddings=[topic_embedding], n_results=1)
                        dist_val = res and res.get("distances") and res["distances"][0][0]
                        distance = float(dist_val) if isinstance(dist_val, (int, float)) else 0.0
                    except Exception:
                        distance = 0.0
                else:
                    distance = 0.0
            else:
                # No memory = all topics are equally valid, random wins
                distance = 1.0

            logger.info(f"[Sistema 2] Evaluating topic {cid}: distance={distance:.4f}")

            if distance > best_distance:
                best_distance = distance
                best_topic = entry

        if best_topic:
            logger.info(
                "Tema seleccionado (muestra=%s) | distancia≈%.4f (MÁS LEJANO)",
                len(candidates),
                best_distance,
            )
            logger.info(f"[PERF] find_relevant_topic took {time.time() - start_time:.2f} seconds.")
            return best_topic

        # Fallback: return random from pool
        try:
            fallback_id = random.choice(pool)
            logger.info("Tema seleccionado (fallback aleatorio)")
            logger.info(f"[PERF] find_relevant_topic (fallback) took {time.time() - start_time:.2f} seconds.")
            return _extract_topic_entry(topics_collection, fallback_id)
        except Exception:
            logger.warning("Fallback pool vacío; no se puede elegir tema.")
            return None

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
