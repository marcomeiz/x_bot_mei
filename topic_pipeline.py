import hashlib
import os
from dataclasses import dataclass, field
from typing import Iterable, List, Optional

from llm_fallback import llm
from logger_config import logger
from prompt_context import PromptContext
from style_guard import audit_style
from ingestion_config import WatcherConfig

LLAMA_AVAILABLE = True
try:
    from llama_index.core import Document, Settings, VectorStoreIndex
    from llama_index.llms.openai import OpenAI as LlamaOpenAI
except ImportError:
    LLAMA_AVAILABLE = False

from pydantic import BaseModel, Field


@dataclass(frozen=True)
class TopicRecord:
    topic_id: str
    abstract: str
    source_pdf: str
    metadata: dict = field(default_factory=dict)


class Topic(BaseModel):
    abstract: str = Field(..., description="Tweet-worthy topic extracted from the text.")


class RagTopicList(BaseModel):
    topics: List[Topic]


class ValidationResponse(BaseModel):
    is_relevant: bool


def configure_llama_index() -> None:
    if not LLAMA_AVAILABLE:
        logger.info("LlamaIndex no disponible; se usará fallback LLM para extracción de temas.")
        return
    pass # No se configura Settings.llm aquí directamente para evitar dependencia de Ollama.


def extract_topics(text: str, context: PromptContext) -> List[str]:
    if LLAMA_AVAILABLE:
        try:
            topics = _extract_topics_with_llama(text)
            if topics:
                return topics
            logger.warning("LlamaIndex no devolvió temas. Se usará fallback LLM.")
        except Exception as exc:
            logger.warning("Fallo en LlamaIndex: %s. Se usará fallback LLM.", exc)
    return _extract_topics_with_llm(text, context)


def collect_valid_topics(
    topics: Iterable[str],
    pdf_name: str,
    cfg: WatcherConfig,
    context: PromptContext,
    base_metadata: Optional[dict] = None,
) -> List[TopicRecord]:
    records: List[TopicRecord] = []
    seen_ids = set()
    for abstract in topics:
        if not abstract:
            continue
        logger.info("Validando tópico: %s", abstract[:80])
        if not _validate_topic(abstract, cfg):
            logger.info(" -> Rechazado por relevancia")
            continue
        if _style_rejects(abstract, cfg, context.contract):
            logger.info(" -> Rechazado por estilo")
            continue
        topic_id = _build_topic_id(pdf_name, abstract)
        if topic_id in seen_ids:
            logger.info(" -> Duplicado en el mismo PDF. Se omite.")
            continue
        seen_ids.add(topic_id)
        metadata = {"pdf": pdf_name}
        if base_metadata:
            metadata.update(base_metadata)
        if "source_type" not in metadata:
            metadata["source_type"] = "pdf"
        if "status" not in metadata:
            metadata["status"] = "auto_ingested"
        records.append(
            TopicRecord(
                topic_id=topic_id,
                abstract=abstract,
                source_pdf=pdf_name,
                metadata=metadata,
            )
        )
        logger.info(" -> Aprobado")
    return records


def _build_topic_id(pdf_name: str, abstract: str) -> str:
    return hashlib.md5(f"{pdf_name}:{abstract}".encode()).hexdigest()[:10]


def _extract_topics_with_llama(text: str) -> List[str]:
    documents = [Document(text=text)]
    index = VectorStoreIndex.from_documents(documents)
    query_engine = index.as_query_engine(output_cls=RagTopicList, response_mode="compact")
    query = (
        "Extract 8-12 high-quality, tweet-worthy topics from the document. "
        "Focus on counter-intuitive insights, practical advice, or strong opinions relevant to a COO. "
        "Each topic should be a concise, self-contained statement. ENGLISH ONLY."
    )
    response = query_engine.query(query)
    topics = [topic.abstract.strip() for topic in (response.topics if response else []) if topic.abstract]
    logger.info("LlamaIndex extrajo %s temas potenciales.", len(topics))
    return topics


def _extract_topics_with_llm(text: str, context: PromptContext) -> List[str]:
    prompt = (
        "You are an operations strategist extracting tweet-worthy topics for a COO persona. "
        "Read the following transcript and return 8-12 concise topic statements (max 200 characters each). "
        "Focus on counter-intuitive insights, sharp advice or punchy observations relevant to operations, leadership, systems, execution, and growth. "
        "Avoid duplications or vague platitudes. ALL TOPICS MUST BE IN ENGLISH."
    )
    try:
        payload = llm.chat_json(
            model=os.getenv("GEMINI_MODEL", "gemini-2.5-pro"),
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You extract topics from documents. Respond ONLY with JSON in the shape {\"topics\": [\"...\"]}. \n"
                        "Respect the voice contract and complementary guidelines provided."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Contract:\n{context.contract}\n\n"
                        f"Guidelines:\n{context.final_guidelines}\n\n"
                        f"Document:\n{text}\n\n"
                        f"Task:\n{prompt}"
                    ),
                },
            ],
            temperature=0.4,
        )
    except Exception as exc:
        logger.error("Fallback LLM para extracción falló: %s", exc, exc_info=True)
        return []
    topics = []
    if isinstance(payload, dict):
        raw_topics = payload.get("topics")
        if isinstance(raw_topics, list):
            topics = [str(item).strip() for item in raw_topics if str(item).strip()]
    if len(topics) > 12:
        topics = topics[:12]
    logger.info("Fallback LLM extrajo %s temas potenciales.", len(topics))
    return topics


def _validate_topic(abstract: str, cfg: WatcherConfig) -> bool:
    if cfg.lenient_validation:
        prompt = (
            "Decide if this topic would be of practical interest to a COO. "
            "Approve unless it is clearly unrelated to operations, leadership, people, systems, processes, execution, org design, "
            "productivity, finance ops, product ops, portfolio/roadmap, or growth. If unsure, approve.\n\n"
            f'Topic: "{abstract}"'
        )
    else:
        prompt = f'Is this topic "{abstract}" relevant for a COO persona?'

    fallback = llm.chat_json(
        model=os.getenv("GEMINI_MODEL", "gemini-2.5-pro"),
        messages=[
            {
                "role": "system",
                "content": "Answer ONLY with JSON of shape {\"is_relevant\": true/false}.",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.0,
    )
    if isinstance(fallback, dict):
        val = fallback.get("is_relevant")
        if isinstance(val, bool):
            return val
    return False


def _style_rejects(abstract: str, cfg: WatcherConfig, contract_text: str) -> bool:
    if not cfg.enforce_style_audit:
        return False
    try:
        audit = audit_style(abstract, contract_text) or {}
    except Exception as exc:
        logger.warning("Style audit failed for '%s...': %s", abstract[:40], exc)
        return False

    needs_revision = bool(audit.get("needs_revision", False))
    voice = str(audit.get("voice", "")).lower()
    corp = int(audit.get("corporate_jargon_score", 0) or 0)
    clich = int(audit.get("cliche_score", 0) or 0)

    too_boardroom = needs_revision and voice == "boardroom" and (
        corp >= cfg.jargon_threshold or clich >= cfg.cliche_threshold
    )
    if too_boardroom:
        logger.info(
            "Estilo rechazado por voz corporativa (jargon=%s, cliche=%s).",
            corp,
            clich,
        )
    return too_boardroom
