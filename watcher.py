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
    prompt = f'Is this topic "{topic_abstract}" relevant for a COO persona? Respond ONLY with JSON: {{"is_relevant": boolean}}'
    try:
        data = llm.chat_json(
            model=VALIDATION_MODEL,
            messages=[
                {"role": "system", "content": "You are a strict JSON editor."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
        )
        return bool(data.get("is_relevant", False)) if isinstance(data, dict) else False
    except Exception:
        return False

async def extract_and_validate_topics(text, pdf_name):
    chunks = chunk_text(text)
    all_validated_topics = []
    
    total_extracted = 0
    total_approved = 0
    
    topics_to_embed = []

    for i, chunk in enumerate(chunks):
        print(f"[procesando] Chunk {i+1}/{len(chunks)} ({round(((i+1)/len(chunks))*100,1)}%)")
        prompt_extract = f'Extract 8-12 tweet-worthy topics from this text chunk: "{chunk}". Respond ONLY with a JSON array of objects like [{{"abstract": "..."}}].'
        try:
            extracted_topics = llm.chat_json(
                model=GENERATION_MODEL,
                messages=[
                    {"role": "system", "content": "You are a concise JSON assistant."},
                    {"role": "user", "content": prompt_extract},
                ],
                temperature=0.7,
            )
            if not isinstance(extracted_topics, list):
                extracted_topics = []
        except Exception as e:
            print(f"Extraction API call failed for chunk {i+1}: {e}")
            continue

        total_extracted += len(extracted_topics)
        
        for topic in extracted_topics:
            abstract = topic.get("abstract", "")
            if not abstract: continue
            
            print(f"  [validando] '{abstract[:60]}...'")
            if validate_topic(abstract):
                topic_id = hashlib.md5(abstract.encode()).hexdigest()[:10]
                topic["topic_id"] = topic_id
                all_validated_topics.append(topic)
                total_approved += 1
                
                topics_to_embed.append({
                    "id": topic_id,
                    "document": abstract
                })
                
                print(f"    -> ✅ Aprobado")
            else:
                print(f"    -> ❌ Rechazado")
    
    if topics_to_embed:
        print(f"\n[procesando embeddings] Generando {len(topics_to_embed)} embeddings para los temas aprobados...")
        
        documents = [item["document"] for item in topics_to_embed]
        ids = [item["id"] for item in topics_to_embed]
        
        embeddings = [get_embedding(doc) for doc in documents]
        
        valid_embeddings = [emb for emb in embeddings if emb is not None]
        
        if valid_embeddings:
            # --- MODIFICACIÓN 2: LLAMAR A LA FUNCIÓN ---
            # Ahora llamamos a la función para obtener el objeto de la colección
            topics_collection = get_topics_collection()
            # Añadir metadatos básicos con el nombre del PDF de origen
            metadatas = [{"pdf": pdf_name} for _ in documents]
            topics_collection.add(
                embeddings=valid_embeddings,
                documents=documents,
                ids=ids,
                metadatas=metadatas
            )
            print(f"✅ {len(valid_embeddings)} temas han sido añadidos a la base de datos vectorial 'topics_collection'.")
        else:
            print("⚠️ No se pudieron generar embeddings válidos para los temas.")

    out_path = os.path.join(JSON_DIR, f"{pdf_name}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"pdf_name": pdf_name, "extracted_topics": all_validated_topics}, f, indent=2, ensure_ascii=False)
    
    summary = f"Temas extraídos: {total_extracted}, Aprobados: {total_approved} ({round((total_approved/total_extracted)*100, 1) if total_extracted > 0 else 0}%)"
    print("-" * 50)
    print(f"[ok] Proceso completado para '{pdf_name}'")
    print(f"  -> {summary}")
    print(f"  -> Archivo guardado: {out_path}")

    if total_approved > 0:
        await notifier.send(title=f"✅ Proceso Completado: {pdf_name}", message=f"Se han añadido {total_approved} nuevos temas a la base de datos.")

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
