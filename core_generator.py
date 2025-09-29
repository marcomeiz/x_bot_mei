# core_generator.py
import os
import random
import re
from dotenv import load_dotenv
from llm_fallback import llm

# --- NUEVO: Importar el logger configurado ---
from logger_config import logger

from embeddings_manager import get_embedding, get_topics_collection, get_memory_collection

load_dotenv()
openrouter_api_key = os.getenv("OPENROUTER_API_KEY")

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
CONTRACT_FILE = os.path.join(BASE_DIR, "copywriter_contract.md")
GENERATION_MODEL = "anthropic/claude-3.5-sonnet"
VALIDATION_MODEL = "anthropic/claude-3-haiku"
SIMILARITY_THRESHOLD = 0.25

# --- (Funciones auxiliares refine_... no cambian) ---
def refine_and_shorten_tweet(tweet_text: str, model: str) -> str:
    prompt = f'Your task is to shorten the following text to be under 280 characters. This is a hard limit. Preserve the core message and tone. Original Text: "{tweet_text}"'
    try:
        text = llm.chat_text(
            model=model,
            messages=[
                {"role": "system", "content": "You are a ruthless text editor."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.4,
        )
        return text
    except Exception: return tweet_text

# Eliminado recorte forzado: preferimos iterar con LLM hasta cumplir la longitud

def refine_single_tweet_style(raw_text: str, model: str) -> str:
    prompt = (
        f"You are a style editor. Rewrite the text below to match a specific style (airy, personal, witty) defined by a contract. "
        f"The core insight MUST remain. RULES: 1) 2-4 short paragraphs. 2) Personal voice. 3) Subtle wit. 4) Do NOT wrap output in quotes. "
        f"5) Prefer concise phrasing. RAW TEXT: --- {raw_text} ---"
    )
    system_message = "You are a world-class ghostwriter rewriting text into a specific style defined by a contract. Keep it concise."
    try:
        text = llm.chat_text(
            model=model,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": prompt},
            ],
            temperature=0.6,
        )
        return text
    except Exception: return raw_text

def ensure_under_limit_via_llm(text: str, model: str, limit: int = 280, attempts: int = 4) -> str:
    """Itera con LLM hasta obtener un texto <= limit, sin recorte local."""
    attempt = 0
    best = text
    while attempt < attempts:
        attempt += 1
        prompt = (
            f"Rewrite the text so the TOTAL characters are <= {limit}. Preserve meaning and readability. "
            f"Do NOT add quotes, emojis or hashtags. Prefer short words and compact phrasing. Return ONLY JSON: "
            f"{{\"text\": \"<final text under {limit} chars>\"}}. Text must be <= {limit} characters.\n\n"
            f"TEXT: {best}"
        )
        try:
            data = llm.chat_json(
                model=model,
                messages=[
                    {"role": "system", "content": "You are a ruthless editor returning strict JSON only."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.25,
            )
            candidate = (data or {}).get("text") if isinstance(data, dict) else None
            if isinstance(candidate, str) and candidate.strip():
                candidate = candidate.strip()
                if len(candidate) <= limit:
                    return candidate
                best = candidate
        except Exception:
            # Intento fallido, vuelve a intentar con el mejor candidato actual
            pass
    return best

# --- FUNCIÓN DE PARSEO MODIFICADA ---
def parse_final_drafts(draft: str) -> (str, str):
    """Parsea dos alternativas en inglés, [EN - A] y [EN - B]."""
    draft_a_match = re.search(r"\[EN\s*-\s*A\]\s*(.*)", draft, re.DOTALL | re.IGNORECASE)
    draft_b_match = re.search(r"\[EN\s*-\s*B\]\s*(.*)", draft, re.DOTALL | re.IGNORECASE)

    english_a = draft_a_match.group(1).split("[EN - B]")[0].strip() if draft_a_match else ""
    english_b = draft_b_match.group(1).strip() if draft_b_match else ""

    return english_a, english_b

# --- FUNCIÓN DE GENERACIÓN MODIFICADA ---
def generate_tweet_from_topic(topic_abstract: str):
    # --- Comprobación de Memoria ---
    logger.info("Iniciando comprobación de similitud en memoria.")
    memory_collection = get_memory_collection()
    topic_embedding = get_embedding(topic_abstract)
    if topic_embedding and memory_collection.count() > 0:
        results = memory_collection.query(query_embeddings=[topic_embedding], n_results=1)
        if results and results['distances'][0][0] < SIMILARITY_THRESHOLD:
            distance = results['distances'][0][0]
            similar_id = results['ids'][0][0]
            logger.warning(f"Similitud detectada. Distancia: {distance:.4f} (Umbral: {SIMILARITY_THRESHOLD}). Tuit similar ID: {similar_id}.")
            return f"Error: El tema es demasiado similar a un tuit ya publicado.", ""
    logger.info("Comprobación de similitud superada. El tema es original.")

    MAX_ATTEMPTS = 3
    for attempt in range(MAX_ATTEMPTS):
        logger.info(f"Intento de generación de IA {attempt + 1}/{MAX_ATTEMPTS}...")
        try:
            with open(CONTRACT_FILE, "r", encoding="utf-8") as f: contract = f.read()

            # --- PROMPT MODIFICADO PARA PEDIR DOS OPCIONES EN INGLÉS ---
            prompt = f"""
            You are a ghostwriter. Your task is to write TWO distinct alternatives for a tweet based on the topic below. Strictly follow the provided contract.

            **Contract for style and tone:**
            {contract}
            ---
            **Assignment:**
            - **Topic:** {topic_abstract}

            **CRITICAL OUTPUT REQUIREMENTS:**
            1.  Provide two high-quality, distinct alternatives in English.
            2.  Label the first alternative with `[EN - A]`.
            3.  Label the second alternative with `[EN - B]`.
            4.  Both alternatives MUST be under 280 characters. Do NOT wrap in quotes.

            Provide ONLY the final text in the specified format (no extra commentary).
            """
            
            logger.info(f"Llamando al modelo de generación: {GENERATION_MODEL}.")
            raw_draft = llm.chat_text(
                model=GENERATION_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.75,
            )

            draft_a, draft_b = parse_final_drafts(raw_draft)

            if not draft_a or not draft_b:
                logger.warning(f"Intento {attempt + 1}: El borrador no contenía las dos alternativas [EN - A] y [EN - B]. Reintentando...")
                continue
            
            logger.info(f"Intento {attempt + 1}: Borradores A y B parseados. Iniciando refinamiento.")
            # Refinar estilo y asegurar límite mediante LLM iterativo (sin recorte local)
            draft_a = refine_single_tweet_style(draft_a, VALIDATION_MODEL)
            draft_b = refine_single_tweet_style(draft_b, VALIDATION_MODEL)

            if len(draft_a) > 280:
                draft_a = ensure_under_limit_via_llm(draft_a, VALIDATION_MODEL, 280, attempts=4)
            if len(draft_b) > 280:
                draft_b = ensure_under_limit_via_llm(draft_b, VALIDATION_MODEL, 280, attempts=4)

            if len(draft_a) > 280 or len(draft_b) > 280:
                logger.warning(f"Intento {attempt + 1}: Alguna alternativa seguía >280 tras reescritura LLM. Reintentando...")
                continue

            logger.info(f"Intento {attempt + 1}: Borradores generados y validados con éxito.")
            return draft_a, draft_b

        except Exception as e:
            logger.error(f"Error crítico en el intento de generación {attempt + 1}: {e}", exc_info=True)

    logger.error("No se pudo generar un borrador válido tras varios intentos.")
    return "Error: No se pudo generar un borrador válido tras varios intentos.", ""

# --- (find_relevant_topic y find_topic_by_id no cambian en su lógica principal) ---
def find_relevant_topic():
    logger.info("Buscando un tema aleatorio en 'topics_collection'...")
    topics_collection = get_topics_collection()
    try:
        all_ids = topics_collection.get(include=[])['ids']
        if not all_ids:
            logger.warning("'topics_collection' está vacía. No se pueden encontrar temas.")
            return None
        random_id = random.choice(all_ids)
        logger.info(f"ID de tema aleatorio seleccionado: {random_id}")
        topic_data = topics_collection.get(ids=[random_id], include=["documents", "metadatas"])  # type: ignore
        if topic_data and topic_data.get('documents'):
            docs = topic_data['documents']
            mds = topic_data.get('metadatas') or []
            # Compatibilidad con formatos (lista o lista de listas)
            topic_abstract = docs[0][0] if docs and isinstance(docs[0], list) else docs[0]
            pdf_name = None
            if mds:
                first_md = mds[0][0] if isinstance(mds[0], list) and mds[0] else (mds[0] if isinstance(mds, list) else None)
                if isinstance(first_md, dict):
                    pdf_name = first_md.get('pdf') or first_md.get('source_pdf')
            topic = {"topic_id": random_id, "abstract": topic_abstract}
            if pdf_name:
                topic["source_pdf"] = pdf_name
            return topic
    except Exception as e:
        logger.error(f"Error al buscar un tema en ChromaDB: {e}", exc_info=True)
    return None

def find_topic_by_id(topic_id: str):
    # Aunque esta función no se usa, es buena práctica añadir logs si se decidiera usar en el futuro
    logger.info(f"Buscando tema específico por ID: {topic_id}")
    topics_collection = get_topics_collection()
    try:
        topic_data = topics_collection.get(ids=[topic_id], include=["documents", "metadatas"])  # type: ignore
        if topic_data and topic_data.get('documents'):
            logger.info(f"Tema con ID {topic_id} encontrado en ChromaDB.")
            docs = topic_data['documents']
            mds = topic_data.get('metadatas') or []
            topic_abstract = docs[0][0] if docs and isinstance(docs[0], list) else docs[0]
            pdf_name = None
            if mds:
                first_md = mds[0][0] if isinstance(mds[0], list) and mds[0] else (mds[0] if isinstance(mds, list) else None)
                if isinstance(first_md, dict):
                    pdf_name = first_md.get('pdf') or first_md.get('source_pdf')
            result = {"topic_id": topic_id, "abstract": topic_abstract}
            if pdf_name:
                result["source_pdf"] = pdf_name
            return result
    except Exception as e:
        logger.error(f"Error al buscar tema por ID {topic_id} en ChromaDB: {e}", exc_info=True)
    
    logger.warning(f"Tema con ID {topic_id} no encontrado en ChromaDB. Fallback no implementado.")
    # Fallback a JSON...
    return None
