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
TOPIC_CANDIDATE_MULTIPLIER = max(1, int(os.getenv("TOPIC_CANDIDATE_MULTIPLIER", "4") or 4))
TOPIC_CANDIDATE_MIN = max(1, int(os.getenv("TOPIC_CANDIDATE_MIN", "12") or 12))
TOPIC_RECENCY_HALF_LIFE_HOURS = max(1.0, float(os.getenv("TOPIC_RECENCY_HALF_LIFE_HOURS", "96") or 96.0))
TOPIC_SCORE_DISTANCE_WEIGHT = float(os.getenv("TOPIC_SCORE_DISTANCE_WEIGHT", "0.75") or 0.75)
TOPIC_SCORE_RECENCY_WEIGHT = float(os.getenv("TOPIC_SCORE_RECENCY_WEIGHT", "0.20") or 0.20)
TOPIC_SCORE_PRIORITY_WEIGHT = float(os.getenv("TOPIC_SCORE_PRIORITY_WEIGHT", "0.05") or 0.05)
TOPIC_SCORE_JITTER_WEIGHT = float(os.getenv("TOPIC_SCORE_JITTER_WEIGHT", "0.05") or 0.05)

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


def _compute_recency_score(metadata: Optional[Dict[str, object]], now: Optional[datetime] = None) -> float:
    if not isinstance(metadata, dict):
        return 0.0
    timestamp = None
    for key in ("created_at", "createdAt", "ingested_at", "ingestedAt", "updated_at", "updatedAt", "timestamp", "ts"):
        if key in metadata:
            timestamp = _parse_metadata_timestamp(metadata.get(key))
            if timestamp:
                break
    if not timestamp:
        return 0.0
    now_dt = now or datetime.now(timezone.utc)
    age_hours = (now_dt - timestamp).total_seconds() / 3600.0
    if age_hours <= 0:
        return 1.0
    return math.exp(-age_hours / TOPIC_RECENCY_HALF_LIFE_HOURS)


def _compute_priority_boost(metadata: Optional[Dict[str, object]]) -> float:
    if not isinstance(metadata, dict):
        return 0.0
    for key in ("priority_score", "priority", "importance", "score"):
        if key in metadata:
            try:
                value = float(metadata[key])
                return max(0.0, min(value, 1.0))
            except (TypeError, ValueError):
                continue
    return 0.0

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


def generate_tweet_from_topic(topic_abstract: str, ignore_similarity: bool = True) -> Dict[str, object]:
    context = build_prompt_context()
    settings = _build_settings()

    _log_similarity(topic_abstract, ignore_similarity)
    # RAG: delegar recuperación de anclas al generador de variantes (NN por defecto)
    gold_examples = None

    # --- Adaptive mode has been removed in favor of the single-call standard generator ---

    last_error = ""
    provider_error_message = ""
    for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
        logger.info("Intento de generación de IA %s/%s…", attempt, MAX_GENERATION_ATTEMPTS)
        try:
            with Timer("g_llm_single_call", labels={"model": GENERATION_MODEL}):
                drafts, variant_errors = generate_all_variants(
                    topic_abstract,
                    context,
                    settings,
                    gold_examples=gold_examples,
                )
            result = {
                "short": (drafts.get("short") or "").strip(),
                "mid": (drafts.get("mid") or "").strip(),
                "long": (drafts.get("long") or "").strip(),
            }
            if variant_errors:
                result["variant_errors"] = variant_errors
            if any(result[label] for label in ("short", "mid", "long")):
                logger.info("Intento %s: variantes generadas (con %s errores).", attempt, len(variant_errors))
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


def find_relevant_topic(sample_size: int = 3):
    start_time = time.time()

    logger.info("Buscando tema en 'topics_collection' (preferir menos similar a memoria)…")
    topics_collection = get_topics_collection()
    try:
        # 1) Fetch a small window of approved topics (fast path)
        raw = topics_collection.get(where={"status": {"$eq": "approved"}}, include=["metadatas", "documents"], limit=200)  # type: ignore[arg-type]
        ids_approved = flatten_chroma_array(raw.get("ids") if isinstance(raw, dict) else None)
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
            all_ids = flatten_chroma_array(raw.get("ids") if isinstance(raw, dict) else None)
            pool = list(all_ids or [])
        else:
            pool = list(ids_approved)

        if not pool:
            logger.warning("'topics_collection' no devolvió IDs. No se pueden encontrar temas.")
            return None

        candidate_pool_size = min(len(pool), max(sample_size * TOPIC_CANDIDATE_MULTIPLIER, TOPIC_CANDIDATE_MIN))
        candidates = random.sample(pool, candidate_pool_size)

        # Pre-cargar embeddings existentes para los candidatos para evitar recomputación en cada /g
        embedding_map: Dict[str, list] = {}
        try:
            emb_raw = topics_collection.get(ids=candidates, include=["embeddings"])  # type: ignore[arg-type]
            if isinstance(emb_raw, dict):
                emb_ids = flatten_chroma_array(emb_raw.get("ids"))
                emb_vals = emb_raw.get("embeddings") or []
                # Aplanar si viene como lista de listas
                if isinstance(emb_vals, list) and emb_vals and isinstance(emb_vals[0], list):
                    flat_embs = [v for sub in emb_vals for v in sub]
                else:
                    flat_embs = emb_vals or []
                if emb_ids and flat_embs and len(emb_ids) == len(flat_embs):
                    embedding_map = {i: e for i, e in zip(emb_ids, flat_embs) if isinstance(e, list) and e}
        except Exception as exc:
            logger.warning("No se pudieron recuperar embeddings precomputados para candidatos: %s", exc)

        memory_collection = get_memory_collection()
        try:
            has_memory = memory_collection.count() > 0
        except Exception:
            has_memory = False

        best_topic: Optional[Dict[str, object]] = None
        best_score = float("-inf")
        best_distance = 0.0
        best_recency = 0.0
        best_priority = 0.0
        fallback_candidates = []
        now = datetime.now(timezone.utc)

        for cid in candidates:
            entry = _extract_topic_entry(topics_collection, cid)
            if not entry:
                continue

            abstract = entry["abstract"]
            distance = 1.0
            if has_memory:
                try:
                    # Usar embedding ya almacenado para este tópico si existe; evita get_embedding
                    embedding = embedding_map.get(cid)
                    if embedding is not None:
                        res = memory_collection.query(query_embeddings=[embedding], n_results=3)
                        dist_val = res and res.get("distances") and res["distances"][0][0]
                        distance = float(dist_val) if isinstance(dist_val, (int, float)) else 1.0
                except Exception:
                    distance = 1.0

            metadata = entry.get("metadata")
            recency_score = _compute_recency_score(metadata, now)
            priority_boost = _compute_priority_boost(metadata)
            jitter = random.random()

            score = (
                TOPIC_SCORE_DISTANCE_WEIGHT * distance
                + TOPIC_SCORE_RECENCY_WEIGHT * recency_score
                + TOPIC_SCORE_PRIORITY_WEIGHT * priority_boost
                + TOPIC_SCORE_JITTER_WEIGHT * jitter
            )

            logger.debug(
                "Evaluando topic_id=%s | distancia=%.4f recency=%.4f priority=%.4f score=%.4f",
                cid,
                distance,
                recency_score,
                priority_boost,
                score,
            )

            if has_memory and distance < SIMILARITY_THRESHOLD:
                fallback_candidates.append((score, entry, distance, recency_score, priority_boost))
                continue

            if score > best_score:
                best_score = score
                best_topic = entry
                best_distance = distance
                best_recency = recency_score
                best_priority = priority_boost

        if best_topic:
            logger.info(
                "Tema seleccionado (pool=%s) distancia≈%.4f recency≈%.4f priority≈%.4f score≈%.4f",
                len(candidates),
                best_distance,
                best_recency,
                best_priority,
                best_score,
            )
            logger.info(f"[PERF] find_relevant_topic took {time.time() - start_time:.2f} seconds.")
            return best_topic

        if fallback_candidates:
            fallback_candidates.sort(key=lambda item: item[0], reverse=True)
            score, entry, distance, recency_score, priority_boost = fallback_candidates[0]
            logger.info(
                "Tema seleccionado pese a similitud (distancia=%.4f < umbral %.2f) | recency≈%.4f priority≈%.4f score≈%.4f",
                distance,
                SIMILARITY_THRESHOLD,
                recency_score,
                priority_boost,
                score,
            )
            logger.info(f"[PERF] find_relevant_topic (fallback similarity) took {time.time() - start_time:.2f} seconds.")
            return entry

        # Fallback absoluto: devolver cualquier tema
        try:
            fallback_id = random.choice(pool)
        except Exception:
            logger.warning("Fallback pool vacío o no disponible; no se puede elegir tema aleatorio.")
            return None
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
