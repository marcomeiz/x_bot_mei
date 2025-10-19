"""Watcher that ingests PDFs, extracts topics and persists them with metadata.

This version keeps the observable behaviour from the previous script while
splitting the responsibilities into small helpers that are easier to reason
about and test:

- configuration loading (`WatcherConfig`)
- PDF I/O utilities
- topic extraction via LlamaIndex
- validation + style gating
- persistence (local Chroma + optional remote sync)
- filesystem watcher orchestration
"""

import asyncio
import hashlib
import json
import os
import time
from dataclasses import dataclass
from typing import Iterable, List, Optional

import fitz  # PyMuPDF
import requests
from desktop_notifier import DesktopNotifier
from dotenv import load_dotenv
from llm_fallback import llm
from tenacity import retry, stop_after_attempt, wait_random_exponential
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from embeddings_manager import get_embedding, get_topics_collection
from logger_config import logger
from prompt_context import build_prompt_context
from style_guard import audit_style

LLAMA_AVAILABLE = True
try:
    from llama_index.core import Document, Settings, VectorStoreIndex
    from llama_index.llms.openai import OpenAI as LlamaOpenAI
except ImportError:
    LLAMA_AVAILABLE = False

from pydantic import BaseModel, Field


@dataclass(frozen=True)
class WatcherConfig:
    enforce_style_audit: bool
    lenient_validation: bool
    jargon_threshold: int
    cliche_threshold: int
    remote_ingest_url: str
    admin_api_token: str
    remote_batch: int
    remote_timeout: int
    remote_retries: int
    upload_dir: str
    text_dir: str
    json_dir: str


@dataclass(frozen=True)
class TopicRecord:
    topic_id: str
    abstract: str
    source_pdf: str


@dataclass(frozen=True)
class PersistenceSummary:
    sent: int
    added: int
    skipped: int
    errored: int


class Topic(BaseModel):
    abstract: str = Field(..., description="Tweet-worthy topic extracted from the text.")


class RagTopicList(BaseModel):
    topics: List[Topic]


class ValidationResponse(BaseModel):
    is_relevant: bool


# Notifier reused across events
notifier = DesktopNotifier()


def load_config() -> WatcherConfig:
    load_dotenv()
    return WatcherConfig(
        enforce_style_audit=os.getenv("WATCHER_ENFORCE_STYLE_AUDIT", "0").lower() in ("1", "true", "yes", "y"),
        lenient_validation=os.getenv("WATCHER_LENIENT_VALIDATION", "1").lower() in ("1", "true", "yes", "y"),
        jargon_threshold=int(os.getenv("WATCHER_JARGON_THRESHOLD", "4") or 4),
        cliche_threshold=int(os.getenv("WATCHER_CLICHE_THRESHOLD", "4") or 4),
        remote_ingest_url=os.getenv("REMOTE_INGEST_URL", "").strip(),
        admin_api_token=os.getenv("ADMIN_API_TOKEN", "").strip(),
        remote_batch=int(os.getenv("REMOTE_INGEST_BATCH", "25") or 25),
        remote_timeout=int(os.getenv("REMOTE_INGEST_TIMEOUT", "120") or 120),
        remote_retries=int(os.getenv("REMOTE_INGEST_RETRIES", "3") or 3),
        upload_dir=os.getenv("UPLOAD_DIR", "uploads"),
        text_dir=os.getenv("TEXT_DIR", "texts"),
        json_dir=os.getenv("JSON_DIR", "json"),
    )


def configure_llama_index(ollama_host: Optional[str] = None) -> None:
    if not LLAMA_AVAILABLE:
        logger.info("LlamaIndex no disponible; se usará fallback LLM para extracción de temas.")
        return
    host = ollama_host or os.getenv("OLLAMA_HOST", "http://localhost:11434")
    Settings.llm = LlamaOpenAI(api_base=f"{host}/v1", api_key="ollama", model="phi3")


def ensure_directories(cfg: WatcherConfig) -> None:
    for path in (cfg.upload_dir, cfg.text_dir, cfg.json_dir):
        os.makedirs(path, exist_ok=True)


def extract_text_from_pdf(pdf_path: str, output_txt_path: str) -> str:
    with fitz.open(pdf_path) as doc:
        text = "\n".join(page.get_text() for page in doc)
    with open(output_txt_path, "w", encoding="utf-8") as handle:
        handle.write(text)
    logger.info("Texto extraído -> %s", output_txt_path)
    return text


def _build_topic_id(pdf_name: str, abstract: str) -> str:
    return hashlib.md5(f"{pdf_name}:{abstract}".encode()).hexdigest()[:10]


def _flatten_ids(raw_ids) -> List[str]:
    if isinstance(raw_ids, list) and raw_ids and isinstance(raw_ids[0], list):
        return [item for sub in raw_ids for item in sub]
    if isinstance(raw_ids, list):
        return [item for item in raw_ids if item]
    return []


def extract_topics_with_llama(text: str) -> List[str]:
    documents = [Document(text=text)]
    index = VectorStoreIndex.from_documents(documents)
    query_engine = index.as_query_engine(output_cls=RagTopicList, response_mode="compact")
    query = (
        "Extract 8-12 high-quality, tweet-worthy topics from the document. "
        "Focus on counter-intuitive insights, practical advice, or strong opinions relevant to a COO. "
        "Each topic should be a concise, self-contained statement."
    )
    response = query_engine.query(query)
    topics = [topic.abstract.strip() for topic in (response.topics if response else []) if topic.abstract]
    logger.info("LlamaIndex extrajo %s temas potenciales.", len(topics))
    return topics


def extract_topics_with_llm(text: str, context) -> List[str]:
    prompt = (
        "You are an operations strategist extracting tweet-worthy topics for a COO persona. "
        "Read the following transcript and return 8-12 concise topic statements (max 200 characters each). "
        "Focus on counter-intuitive insights, sharp advice or punchy observations relevant to operations, leadership, systems, execution, and growth. "
        "Avoid duplications or vague platitudes."
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


@retry(wait=wait_random_exponential(min=1, max=60), stop=stop_after_attempt(3))
def validate_topic(abstract: str, cfg: WatcherConfig) -> bool:
    if cfg.lenient_validation:
        prompt = (
            "Decide if this topic would be of practical interest to a COO. "
            "Approve unless it is clearly unrelated to operations, leadership, people, systems, processes, execution, org design, "
            "productivity, finance ops, product ops, portfolio/roadmap, or growth. If unsure, approve.\n\n"
            f'Topic: "{abstract}"'
        )
    else:
        prompt = f'Is this topic "{abstract}" relevant for a COO persona?'
    try:
        response = llm.chat_structured(
            model="ollama/phi3",
            messages=[
                {"role": "system", "content": "You are a strict validation assistant. Decide if the topic is relevant."},
                {"role": "user", "content": prompt},
            ],
            response_model=ValidationResponse,
            temperature=0.0,
        )
        if response:
            return bool(response.is_relevant)
    except Exception as exc:
        logger.warning("Validación local falló, usando fallback LLM: %s", exc)

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


def style_rejects(abstract: str, cfg: WatcherConfig, contract_text: str) -> bool:
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
            "Estilo rechazado por voz corporativa (jargon=%s, cliche=%s).", corp, clich
        )
    return too_boardroom


def extract_topics(text: str, context) -> List[str]:
    if LLAMA_AVAILABLE:
        try:
            topics = extract_topics_with_llama(text)
            if topics:
                return topics
            logger.warning("LlamaIndex no devolvió temas. Usando fallback LLM.")
        except Exception as exc:
            logger.warning("Fallo en LlamaIndex: %s. Se usará fallback LLM.", exc)
    return extract_topics_with_llm(text, context)


def collect_valid_topics(
    raw_topics: Iterable[str],
    pdf_name: str,
    cfg: WatcherConfig,
    contract_text: str,
) -> List[TopicRecord]:
    seen_local_ids = set()
    approved: List[TopicRecord] = []

    for abstract in raw_topics:
        if not abstract:
            continue
        logger.info("Validando tópico: %s", abstract[:80])
        try:
            if not validate_topic(abstract):
                logger.info(" -> Rechazado por relevancia")
                continue
        except Exception as exc:
            logger.error("Validación falló para '%s...': %s", abstract[:40], exc)
            continue

        if style_rejects(abstract, cfg, contract_text):
            logger.info(" -> Rechazado por estilo")
            continue

        topic_id = _build_topic_id(pdf_name, abstract)
        if topic_id in seen_local_ids:
            logger.info(" -> Duplicado en el mismo PDF. Se omite")
            continue
        seen_local_ids.add(topic_id)
        approved.append(TopicRecord(topic_id=topic_id, abstract=abstract, source_pdf=pdf_name))
        logger.info(" -> Aprobado")

    return approved


def persist_topics(topics: List[TopicRecord], cfg: WatcherConfig) -> PersistenceSummary:
    if not topics:
        return PersistenceSummary(sent=0, added=0, skipped=0, errored=0)

    topics_collection = get_topics_collection()
    embeddings_payload = []
    for topic in topics:
        embedding = get_embedding(topic.abstract)
        if embedding is None:
            logger.warning("No se pudo generar embedding para '%s...'.", topic.abstract[:40])
            continue
        embeddings_payload.append((embedding, topic))

    if not embeddings_payload:
        logger.warning("No hay embeddings válidos para persistir.")
        return PersistenceSummary(sent=0, added=0, skipped=0, errored=len(topics))

    ids = [item.topic_id for _, item in embeddings_payload]
    try:
        existing_response = topics_collection.get(ids=ids, include=[])  # type: ignore[arg-type]
        existing_ids = set(_flatten_ids(existing_response.get("ids")))
    except Exception as exc:
        logger.warning("No se pudo verificar duplicados en la base de datos: %s", exc)
        existing_ids = set()

    entries_to_add = [pair for pair in embeddings_payload if pair[1].topic_id not in existing_ids]

    if entries_to_add:
        topics_collection.add(
            embeddings=[item[0] for item in entries_to_add],
            documents=[item[1].abstract for item in entries_to_add],
            ids=[item[1].topic_id for item in entries_to_add],
            metadatas=[{"pdf": item[1].source_pdf} for item in entries_to_add],
        )
        logger.info("%s temas añadidos a topics_collection.", len(entries_to_add))
    else:
        logger.info("No hay temas nuevos para añadir tras filtrar duplicados.")

    skipped = len(embeddings_payload) - len(entries_to_add)
    errors = len(topics) - len(embeddings_payload)

    if cfg.remote_ingest_url and cfg.admin_api_token and entries_to_add:
        _sync_remote(entries_to_add, cfg)

    return PersistenceSummary(
        sent=len(entries_to_add),
        added=len(entries_to_add),
        skipped=skipped,
        errored=errors,
    )


def _sync_remote(entries_to_add: List[tuple], cfg: WatcherConfig) -> None:
    logger.info(
        "Sync remoto: enviando %s temas en lotes de %s…",
        len(entries_to_add),
        cfg.remote_batch,
    )
    for start in range(0, len(entries_to_add), cfg.remote_batch):
        batch = entries_to_add[start : start + cfg.remote_batch]
        payload = {
            "topics": [
                {
                    "id": topic.topic_id,
                    "abstract": topic.abstract,
                    "pdf": topic.source_pdf,
                }
                for _, topic in batch
            ]
        }
        url = cfg.remote_ingest_url
        url = f"{url}&token={cfg.admin_api_token}" if "?" in url else f"{url}?token={cfg.admin_api_token}"

        attempt = 0
        while True:
            attempt += 1
            try:
                response = requests.post(url, json=payload, timeout=cfg.remote_timeout)
                if response.status_code == 200:
                    try:
                        data = response.json()
                    except Exception:
                        data = {}
                    logger.info(
                        " · Lote %s: added=%s skipped=%s",
                        (start // cfg.remote_batch) + 1,
                        data.get("added"),
                        data.get("skipped_existing"),
                    )
                    break
                logger.warning(
                    " · Lote %s: HTTP %s -> %s",
                    (start // cfg.remote_batch) + 1,
                    response.status_code,
                    response.text[:200],
                )
            except Exception as exc:
                if attempt < cfg.remote_retries:
                    logger.warning(
                        " · Lote %s: error '%s'. Reintentando (%s/%s)…",
                        (start // cfg.remote_batch) + 1,
                        exc,
                        attempt,
                        cfg.remote_retries,
                    )
                    time.sleep(min(2 * attempt, 6))
                    continue
                logger.error(" · Lote %s: fallo definitivo: %s", (start // cfg.remote_batch) + 1, exc)
                break


def write_summary_json(pdf_name: str, topics: List[TopicRecord], cfg: WatcherConfig) -> str:
    output_path = os.path.join(cfg.json_dir, f"{pdf_name}.json")
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "pdf_name": pdf_name,
                "extracted_topics": [topic.__dict__ for topic in topics],
            },
            handle,
            indent=2,
            ensure_ascii=False,
        )
    return output_path


async def process_pdf(pdf_path: str, cfg: WatcherConfig) -> None:
    pdf_name = os.path.splitext(os.path.basename(pdf_path))[0]
    txt_path = os.path.join(cfg.text_dir, f"{pdf_name}.txt")
    text = extract_text_from_pdf(pdf_path, txt_path)

    context = build_prompt_context()
    raw_topics = extract_topics(text, context)
    approved_topics = collect_valid_topics(raw_topics, pdf_name, cfg, context.contract)

    summary = persist_topics(approved_topics, cfg)
    output_path = write_summary_json(pdf_name, approved_topics, cfg)

    percentage = 0.0
    if raw_topics:
        percentage = round((len(approved_topics) / len(raw_topics)) * 100, 1)

    logger.info(
        "Proceso completado para '%s' | Temas extraídos=%s, aprobados=%s (%.1f%%)",
        pdf_name,
        len(raw_topics),
        len(approved_topics),
        percentage,
    )
    logger.info("Resumen guardado en %s", output_path)

    if summary.added > 0:
        await notifier.send(
            title=f"✅ Proceso completado: {pdf_name}",
            message=f"Se han añadido {summary.added} nuevos temas a la base de datos.",
        )


class PDFHandler(FileSystemEventHandler):
    def __init__(self, cfg: WatcherConfig) -> None:
        super().__init__()
        self.cfg = cfg

    def on_created(self, event):  # pragma: no cover - callback
        if event.is_directory:
            return

        async def _handle_async_creation() -> None:
            path = event.src_path
            if not path.lower().endswith(".pdf"):
                return

            logger.info("Nuevo PDF detectado: %s", os.path.basename(path))

            # Esperar a que termine la copia
            previous_size = -1
            while True:
                current_size = os.path.getsize(path)
                if current_size == previous_size:
                    break
                previous_size = current_size
                time.sleep(1)
            logger.info("Archivo copiado completamente.")

            try:
                await process_pdf(path, self.cfg)
            except Exception as exc:
                logger.error("Fallo procesando %s: %s", path, exc, exc_info=True)

        asyncio.run(_handle_async_creation())


def main() -> None:  # pragma: no cover - CLI entry point
    cfg = load_config()
    ensure_directories(cfg)
    configure_llama_index()

    logger.info("Vigilando la carpeta %s para nuevos PDFs…", cfg.upload_dir)
    event_handler = PDFHandler(cfg)
    observer = Observer()
    observer.schedule(event_handler, cfg.upload_dir, recursive=False)
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


if __name__ == "__main__":  # pragma: no cover
    main()
