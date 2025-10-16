import os
import time
import json
import hashlib
import asyncio
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import fitz  # PyMuPDF
from dotenv import load_dotenv
from llm_fallback import llm
from tenacity import retry, stop_after_attempt, wait_random_exponential
from desktop_notifier import DesktopNotifier

# --- MODIFICACIÓN 1: CORREGIR LA IMPORTACIÓN ---
# Importamos la FUNCIÓN que nos da la colección, no la variable
from embeddings_manager import get_embedding, get_topics_collection
import requests
from style_guard import audit_style, CONTRACT_TEXT

# --- Pydantic Schemas for Structured Output ---
from typing import List
from pydantic import BaseModel, Field

class Topic(BaseModel):
    abstract: str = Field(..., description="The concise, tweet-worthy topic extracted from the text.")

class TopicList(BaseModel):
    topics: List[Topic] = Field(..., description="A list of 8-12 extracted topics.")

class ValidationResponse(BaseModel):
    is_relevant: bool = Field(..., description="True if the topic is relevant to a COO, False otherwise.")



# --- CONFIGURACIÓN ---
# Folders
UPLOAD_DIR = "uploads"
TEXT_DIR = "texts"
JSON_DIR = "json"

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(TEXT_DIR, exist_ok=True)
os.makedirs(JSON_DIR, exist_ok=True)

# API Client
load_dotenv()
# Por defecto priorizamos captar temas (tono se ajusta más tarde en generación),
# por eso la auditoría de estilo queda desactivada salvo que se active explícitamente.
WATCHER_ENFORCE_STYLE_AUDIT = os.getenv("WATCHER_ENFORCE_STYLE_AUDIT", "0").lower() in ("1", "true", "yes", "y")
# Validación indulgente: aprobar salvo que sea claramente ajeno al ámbito del COO
WATCHER_LENIENT_VALIDATION = os.getenv("WATCHER_LENIENT_VALIDATION", "1").lower() in ("1", "true", "yes", "y")
# Relajar umbrales de estilo por configuración (valores más permisivos por defecto)
JARGON_THRESHOLD = int(os.getenv("WATCHER_JARGON_THRESHOLD", "4"))
CLICHE_THRESHOLD = int(os.getenv("WATCHER_CLICHE_THRESHOLD", "4"))

# Remote sync (optional)
REMOTE_INGEST_URL = os.getenv("REMOTE_INGEST_URL", "").strip()  # e.g., https://<service-url>/ingest_topics
ADMIN_API_TOKEN = os.getenv("ADMIN_API_TOKEN", "").strip()
# Ajustes de sincronización remota (chunking y tiempo de espera)
REMOTE_INGEST_BATCH = int(os.getenv("REMOTE_INGEST_BATCH", "25") or 25)
REMOTE_INGEST_TIMEOUT = int(os.getenv("REMOTE_INGEST_TIMEOUT", "120") or 120)
REMOTE_INGEST_RETRIES = int(os.getenv("REMOTE_INGEST_RETRIES", "3") or 3)

# Modelos
GENERATION_MODEL = "anthropic/claude-3.5-sonnet"
VALIDATION_MODEL = "anthropic/claude-3-haiku"

# Perfil del Experto
COO_PERSONA = """
Un Chief Operating Officer (COO) enfocado en liderazgo operacional, ejecución,
escalado de negocios, sistemas, procesos, gestión de equipos de alto rendimiento,
productividad y la intersección entre estrategia y operaciones del día a día.
"""

# Inicializar el notificador
notifier = DesktopNotifier()

# --- FUNCIONES ---

def extract_text_from_pdf(pdf_path, out_txt_path):
    doc = fitz.open(pdf_path)
    parts = []
    for page in doc:
        parts.append(page.get_text())
    text = "\n".join(parts)
    with open(out_txt_path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"[ok] Texto extraído -> {out_txt_path}")
    return text

def chunk_text(text, chunk_size=3500, overlap=200):
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        chunks.append(chunk)
        start += chunk_size - overlap
    return chunks

@retry(wait=wait_random_exponential(min=1, max=60), stop=stop_after_attempt(3))
def validate_topic(topic_abstract: str) -> bool:
    if WATCHER_LENIENT_VALIDATION:
        prompt = (
            "Decide if this topic would be of practical interest to a COO. "
            "Approve unless it is clearly unrelated to operations, leadership, people, systems, processes, execution, org design, productivity, finance ops, product ops, portfolio/roadmap, or growth. "
            "If unsure, approve.\n\n"
            f'Topic: "{topic_abstract}"'
        )
    else:
        prompt = f'Is this topic "{topic_abstract}" relevant for a COO persona?'
    try:
        # Use local model for validation for speed and cost savings
        response = llm.chat_structured(
            model="ollama/phi3", 
            messages=[
                {"role": "system", "content": "You are a strict validation assistant. You must decide if the topic is relevant."},
                {"role": "user", "content": prompt}
            ],
            response_model=ValidationResponse,
            temperature=0.0,
        )
        return response.is_relevant if response else False
    except Exception as e:
        logger.error(f"Topic validation failed for '{topic_abstract[:30]}...': {e}")
        return False

async def extract_and_validate_topics(text, pdf_name):
    chunks = chunk_text(text)
    all_validated_topics = []

    total_extracted = 0
    total_approved = 0

    topics_to_embed = []
    seen_topic_ids = set()

    for i, chunk in enumerate(chunks):
        print(f"[procesando] Chunk {i+1}/{len(chunks)} ({round(((i+1)/len(chunks))*100, 1)}%)")
        prompt_extract = (
            f'Extract 8-12 tweet-worthy topics from this text chunk: "{chunk}".'
        )
        try:
            # Use instructor for structured output
            extracted_topic_list = llm.chat_structured(
                model=GENERATION_MODEL,
                messages=[
                    {"role": "system", "content": "You are a topic extraction expert."},
                    {"role": "user", "content": prompt_extract}
                ],
                response_model=TopicList,
                temperature=0.7,
            )
            extracted_topics = extracted_topic_list.topics if extracted_topic_list else []
        except Exception as e:
            print(f"Extraction API call failed for chunk {i+1}: {e}")
            continue

        total_extracted += len(extracted_topics)

        for topic in extracted_topics:
            abstract = topic.abstract
            if not abstract:
                continue

            print(f"  [validando] '{abstract[:60]}...'")
            if validate_topic(abstract):
                # Auditoría de estilo opcional para filtrar abstracts demasiado "boardroom"
                if WATCHER_ENFORCE_STYLE_AUDIT:
                    try:
                        audit = audit_style(abstract, CONTRACT_TEXT) or {}
                        corp = int(audit.get("corporate_jargon_score", 0) or 0)
                        clich = int(audit.get("cliche_score", 0) or 0)
                        voice = str(audit.get("voice", "") or "")
                        needs_rev = bool(audit.get("needs_revision", False))

                        # Nueva lógica (relajada): solo omitir si la voz es claramente "boardroom"
                        # y al menos uno de los puntajes supera los umbrales configurables.
                        too_board = needs_rev and (voice == "boardroom") and (
                            corp >= JARGON_THRESHOLD or clich >= CLICHE_THRESHOLD
                        )
                        if too_board:
                            print(
                                f"    -> ⚠️ Estilo genérico/boardroom (voice={voice}, jargon={corp}, cliche={clich}). Se omite."
                            )
                            continue
                        # Si marca revisión pero no supera umbral, aceptamos y avisamos.
                        if needs_rev:
                            print(
                                f"    -> ℹ️ Marcado como 'needs_revision' (voice={voice}, jargon={corp}, cliche={clich}) pero aceptado."
                            )
                    except Exception:
                        pass
                topic_id = hashlib.md5(f"{pdf_name}:{abstract}".encode()).hexdigest()[:10]
                if topic_id in seen_topic_ids:
                    print("    -> ⚠️ Duplicado dentro del mismo PDF. Se omite.")
                    continue
                seen_topic_ids.add(topic_id)
                topic["topic_id"] = topic_id
                topic["source_pdf"] = pdf_name
                all_validated_topics.append(topic)
                total_approved += 1

                topics_to_embed.append(
                    {
                        "id": topic_id,
                        "document": abstract,
                        "metadata": {"pdf": pdf_name},
                    }
                )

                print("    -> ✅ Aprobado")
            else:
                print("    -> ❌ Rechazado")

    if topics_to_embed:
        print(f"\n[procesando embeddings] Generando {len(topics_to_embed)} embeddings para los temas aprobados...")

        documents = [item["document"] for item in topics_to_embed]
        ids = [item["id"] for item in topics_to_embed]
        metadatas = [item["metadata"] for item in topics_to_embed]

        embeddings = [get_embedding(doc) for doc in documents]

        valid_entries = [
            (embedding, doc, topic_id, metadata)
            for embedding, doc, topic_id, metadata in zip(embeddings, documents, ids, metadatas)
            if embedding is not None
        ]

        if valid_entries:
            topics_collection = get_topics_collection()

            batch_unique_entries = []
            batch_seen_ids = set()
            for entry in valid_entries:
                if entry[2] in batch_seen_ids:
                    print("⚠️ ID duplicado detectado dentro del lote actual. Se omite.")
                    continue
                batch_seen_ids.add(entry[2])
                batch_unique_entries.append(entry)

            existing_ids = set()
            try:
                existing_response = topics_collection.get(ids=[entry[2] for entry in batch_unique_entries], include=[])
                raw_ids = existing_response.get("ids")
                if raw_ids:
                    if isinstance(raw_ids[0], list):
                        existing_ids = {id_ for sublist in raw_ids for id_ in sublist}
                    else:
                        existing_ids = set(raw_ids)
            except Exception as e:
                print(f"⚠️ No se pudo verificar duplicados en la base de datos: {e}")

            entries_to_add = [entry for entry in batch_unique_entries if entry[2] not in existing_ids]

            if not entries_to_add:
                print("⚠️ No hay temas nuevos para añadir tras filtrar duplicados.")
            else:
                topics_collection.add(
                    embeddings=[entry[0] for entry in entries_to_add],
                    documents=[entry[1] for entry in entries_to_add],
                    ids=[entry[2] for entry in entries_to_add],
                    metadatas=[entry[3] for entry in entries_to_add],
                )
                skipped_existing = len(batch_unique_entries) - len(entries_to_add)
                if skipped_existing:
                    print(f"⚠️ Se omiten {skipped_existing} temas ya existentes en la base de datos.")
                print(f"✅ {len(entries_to_add)} temas han sido añadidos a la base de datos vectorial 'topics_collection'.")

                # Remote sync (opcional) con chunking y reintentos: enviar solo los nuevos
                if REMOTE_INGEST_URL and ADMIN_API_TOKEN and entries_to_add:
                    total = len(entries_to_add)
                    print(f"☁️  Sync remoto: enviando {total} temas en lotes de {REMOTE_INGEST_BATCH}…")
                    for start in range(0, total, REMOTE_INGEST_BATCH):
                        batch_entries = entries_to_add[start:start + REMOTE_INGEST_BATCH]
                        payload = {
                            "topics": [
                                {"id": t_id, "abstract": doc, "pdf": (md or {}).get("pdf") if isinstance(md, dict) else None}
                                for _, doc, t_id, md in batch_entries
                            ]
                        }
                        url = REMOTE_INGEST_URL
                        if "?" in url:
                            url = f"{url}&token={ADMIN_API_TOKEN}"
                        else:
                            url = f"{url}?token={ADMIN_API_TOKEN}"
                        attempt = 0
                        while True:
                            attempt += 1
                            try:
                                r = requests.post(url, json=payload, timeout=REMOTE_INGEST_TIMEOUT)
                                if r.status_code == 200:
                                    try:
                                        data = r.json()
                                    except Exception:
                                        data = {}
                                    added = data.get("added")
                                    skipped = data.get("skipped_existing")
                                    print(
                                        f"   · Lote {start//REMOTE_INGEST_BATCH + 1}: added={added}, skipped={skipped}"
                                    )
                                    break
                                else:
                                    print(
                                        f"   · Lote {start//REMOTE_INGEST_BATCH + 1}: HTTP {r.status_code} -> {r.text[:200]}"
                                    )
                            except Exception as e:
                                if attempt < REMOTE_INGEST_RETRIES:
                                    print(
                                        f"   · Lote {start//REMOTE_INGEST_BATCH + 1}: error '{e}'. Reintentando ({attempt}/{REMOTE_INGEST_RETRIES})…"
                                    )
                                    time.sleep(min(2 * attempt, 6))
                                    continue
                                print(f"   · Lote {start//REMOTE_INGEST_BATCH + 1}: fallo definitivo: {e}")
                                break
        else:
            print("⚠️ No se pudieron generar embeddings válidos para los temas.")

    out_path = os.path.join(JSON_DIR, f"{pdf_name}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"pdf_name": pdf_name, "extracted_topics": all_validated_topics}, f, indent=2, ensure_ascii=False)

    summary = (
        f"Temas extraídos: {total_extracted}, Aprobados: {total_approved} "
        f"({round((total_approved / total_extracted) * 100, 1) if total_extracted > 0 else 0}%)"
    )
    print("-" * 50)
    print(f"[ok] Proceso completado para '{pdf_name}'")
    print(f"  -> {summary}")
    print(f"  -> Archivo guardado: {out_path}")

    if total_approved > 0:
        await notifier.send(
            title=f"✅ Proceso Completado: {pdf_name}",
            message=f"Se han añadido {total_approved} nuevos temas a la base de datos.",
        )


class PDFHandler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory:
            return

        async def _handle_async_creation():
            path = event.src_path
            if not path.lower().endswith(".pdf"):
                return

            print(f"\n[nuevo PDF detectado] {os.path.basename(path)}")
            basename = os.path.splitext(os.path.basename(path))[0]
            out_txt = os.path.join(TEXT_DIR, f"{basename}.txt")

            # Esperar a que el archivo se copie completamente
            size1 = -1
            size2 = os.path.getsize(path)
            while size1 != size2:
                time.sleep(1)
                size1 = size2
                size2 = os.path.getsize(path)
            print("[ok] Archivo copiado completamente.")

            try:
                text = extract_text_from_pdf(path, out_txt)
                await extract_and_validate_topics(text, basename)
            except Exception as e:
                print(f"[error] Fallo en el proceso principal para {path}: {e}")

        asyncio.run(_handle_async_creation())


if __name__ == "__main__":
    print(f"Vigilando la carpeta: {UPLOAD_DIR} para nuevos PDFs...")
    event_handler = PDFHandler()
    observer = Observer()
    observer.schedule(event_handler, UPLOAD_DIR, recursive=False)
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
