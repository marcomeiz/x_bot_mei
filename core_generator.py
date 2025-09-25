# core_generator.py
import os
import json
import random
import re
from dotenv import load_dotenv
from openai import OpenAI

from embeddings_manager import get_embedding, get_topics_collection, get_memory_collection

load_dotenv()
openrouter_api_key = os.getenv("OPENROUTER_API_KEY")
client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=openrouter_api_key)

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
CONTRACT_FILE = os.path.join(BASE_DIR, "copywriter_contract.md")
GENERATION_MODEL = "anthropic/claude-3.5-sonnet"
VALIDATION_MODEL = "anthropic/claude-3-haiku"
SIMILARITY_THRESHOLD = 0.25

# --- (Funciones auxiliares refine_... y is_text_in_spanish no cambian) ---
def refine_and_shorten_tweet(tweet_text: str, model: str) -> str:
    # ... (contenido sin cambios) ...
    prompt = f'Your task is to shorten the following text to be under 280 characters. This is a hard limit. Preserve the core message and tone. Original Text: "{tweet_text}"'
    try:
        response = client.chat.completions.create(model=model, messages=[{"role": "system", "content": "You are a ruthless text editor."}, {"role": "user", "content": prompt}], temperature=0.4)
        return response.choices[0].message.content.strip()
    except Exception: return tweet_text

def refine_single_tweet_style(raw_text: str, model: str) -> str:
    # ... (contenido sin cambios) ...
    prompt = f'You are a style editor. Your task is to rewrite the dense text below to match a specific style (airy, personal, witty) defined by a contract. The core insight MUST remain. RULES: 1. Break into 2-4 short paragraphs. 2. Use a personal voice. 3. Add subtle wit. RAW TEXT: --- {raw_text} ---'
    system_message = "You are a world-class ghostwriter rewriting text into a specific style defined by a contract."
    try:
        response = client.chat.completions.create(model=model, messages=[{"role": "system", "content": system_message}, {"role": "user", "content": prompt}], temperature=0.6)
        return response.choices[0].message.content.strip()
    except Exception: return raw_text


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
    memory_collection = get_memory_collection()
    # ... (comprobación de memoria sin cambios) ...
    topic_embedding = get_embedding(topic_abstract)
    if topic_embedding and memory_collection.count() > 0:
        results = memory_collection.query(query_embeddings=[topic_embedding], n_results=1)
        if results and results['distances'][0][0] < SIMILARITY_THRESHOLD:
            return f"Error: El tema es demasiado similar a un tuit ya publicado.", ""

    MAX_ATTEMPTS = 3
    for attempt in range(MAX_ATTEMPTS):
        print(f"\n🚀 Intento de generación {attempt + 1}/{MAX_ATTEMPTS}...")
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
            4.  Both alternatives MUST be under 280 characters. This is a strict rule.
            
            Provide ONLY the final text in the specified format.
            """
            
            response = client.chat.completions.create(model=GENERATION_MODEL, messages=[{"role": "user", "content": prompt}], temperature=0.75)
            raw_draft = response.choices[0].message.content.strip()
            
            draft_a, draft_b = parse_final_drafts(raw_draft)
            
            if not draft_a or not draft_b:
                print("❌ El borrador no contenía las dos alternativas [EN - A] y [EN - B]. Reintentando...")
                continue
            
            # Refinar y validar ambas opciones
            draft_a = refine_single_tweet_style(draft_a, VALIDATION_MODEL)
            draft_b = refine_single_tweet_style(draft_b, VALIDATION_MODEL)

            if len(draft_a) > 280: draft_a = refine_and_shorten_tweet(draft_a, VALIDATION_MODEL)
            if len(draft_b) > 280: draft_b = refine_and_shorten_tweet(draft_b, VALIDATION_MODEL)
            
            if len(draft_a) > 280 or len(draft_b) > 280:
                print("❌ Alguna de las alternativas seguía siendo demasiado larga. Reintentando...")
                continue

            print("👍 Borradores generados con éxito.")
            return draft_a, draft_b

        except Exception as e:
            print(f"❌ Error crítico en el intento {attempt + 1}: {e}")
            
    return "Error: No se pudo generar un borrador válido tras varios intentos.", ""

# --- (find_relevant_topic y find_topic_by_id no cambian) ---
def find_relevant_topic():
    # ... (contenido sin cambios) ...
    topics_collection = get_topics_collection()
    try:
        all_ids = topics_collection.get(include=[])['ids']
        if not all_ids: return None
        random_id = random.choice(all_ids)
        topic_data = topics_collection.get(ids=[random_id])
        if topic_data and topic_data['documents']:
            topic_abstract = topic_data['documents'][0]
            topic = {"topic_id": random_id, "abstract": topic_abstract}
            return topic
    except Exception as e: print(f"❌ Error al buscar un tema en ChromaDB: {e}")
    return None

def find_topic_by_id(topic_id: str):
    # ... (contenido sin cambios) ...
    topics_collection = get_topics_collection()
    try:
        topic_data = topics_collection.get(ids=[topic_id])
        if topic_data and topic_data['documents']:
            return {"topic_id": topic_id, "abstract": topic_data['documents'][0]}
    except Exception: pass
    # Fallback a JSON...
    return None