# core_generator.py
import os
import json
import random
import re
from dotenv import load_dotenv
from openai import OpenAI

from embeddings_manager import get_embedding, get_topics_collection, get_memory_collection

# --- Carga de Configuración y Validación Crítica ---
load_dotenv()
openrouter_api_key = os.getenv("OPENROUTER_API_KEY")

if not openrouter_api_key:
    print("❌ CRITICAL ERROR: La variable de entorno OPENROUTER_API_KEY no se ha encontrado.")
    raise ValueError("OPENROUTER_API_KEY no está configurada en el entorno.")
else:
    print("✅ OPENROUTER_API_KEY cargada con éxito.")

client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=openrouter_api_key)

# --- Rutas y Constantes ---
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
CONTRACT_FILE = os.path.join(BASE_DIR, "copywriter_contract.md")
JSON_DIR = os.path.join(BASE_DIR, "json")
GENERATION_MODEL = "anthropic/claude-3.5-sonnet"
VALIDATION_MODEL = "anthropic/claude-3-haiku"
SIMILARITY_THRESHOLD = 0.25

# --- (Funciones auxiliares como refine_and_shorten, etc. no cambian) ---
def refine_and_shorten_tweet(tweet_text: str, model: str) -> str:
    print(f"📏 Refinando y acortando texto de {len(tweet_text)} caracteres...")
    prompt = f'Your task is to shorten the following text to be under 280 characters. This is a hard limit. You MUST succeed. Do not add any extra commentary. Preserve the core message and tone. Original Text: "{tweet_text}"'
    try:
        response = client.chat.completions.create(model=model, messages=[{"role": "system", "content": "You are a ruthless text editor."}, {"role": "user", "content": prompt}], temperature=0.4)
        return response.choices[0].message.content.strip()
    except Exception:
        return tweet_text

def refine_single_tweet_style(raw_text: str, model: str, lang: str = 'en') -> str:
    print(f"🎨 Refinando estilo y formato de un bloque de texto en '{lang}'...")
    if lang == 'es':
        prompt = f'Eres un editor de estilo experto. Tu tarea es tomar el siguiente texto denso y reescribirlo para que coincida con el estilo definido en un contrato (formato aéreo, voz personal, ingenio). El insight principal DEBE permanecer 100% intacto. REGLAS: 1. Divide en 2-4 párrafos cortos. 2. Usa una voz personal. 3. Añade un toque de ingenio. TEXTO EN BRUTO: --- {raw_text} ---'
        system_message = "Eres un ghostwriter de clase mundial especializado en reescribir texto denso al estilo de un contrato, adaptado al español."
    else:
        prompt = f'You are a style editor. Your task is to take the dense text below and rewrite it to match a specific style (airy, personal, witty) defined by a contract. The core insight MUST remain 100% intact. RULES: 1. Break into 2-4 short paragraphs. 2. Use a personal voice. 3. Add subtle wit. RAW TEXT: --- {raw_text} ---'
        system_message = "You are a world-class ghostwriter rewriting text into a specific style defined by a contract."
    try:
        response = client.chat.completions.create(model=model, messages=[{"role": "system", "content": system_message}, {"role": "user", "content": prompt}], temperature=0.6)
        return response.choices[0].message.content.strip()
    except Exception:
        return raw_text

def parse_final_draft(draft: str) -> (str, str):
    eng_match = re.search(r"\[EN(?:\s*-\s*\d+/\d+)?\]\s*(.*)", draft, re.DOTALL | re.IGNORECASE)
    spa_match = re.search(r"\[ES(?:\s*-\s*\d+/\d+)?\]\s*(.*)", draft, re.DOTALL | re.IGNORECASE)
    english_text = eng_match.group(1).split("[ES")[0].strip() if eng_match else ""
    spanish_text = spa_match.group(1).strip() if spa_match else ""
    return english_text, spanish_text

def is_text_in_spanish(text: str, model: str) -> bool:
    print("🇪🇸 Verificando que el texto esté en español...")
    prompt = f'Is the following text in Spanish? Respond ONLY with JSON: {{"is_spanish": boolean}}. Text: --- {text} ---'
    try:
        response = client.chat.completions.create(model=model, response_format={"type": "json_object"}, messages=[{"role": "system", "content": "You are a JSON language identification expert."}, {"role": "user", "content": prompt}], temperature=0.0)
        return json.loads(response.choices[0].message.content).get("is_spanish", False)
    except Exception:
        return False

# --- FUNCIONES PRINCIPALES (MODIFICADAS) ---
def generate_tweet_from_topic(topic_abstract: str):
    memory_collection = get_memory_collection()
    print(f"🧠 Comprobando si el tema '{topic_abstract[:50]}...' es repetitivo...")
    topic_embedding = get_embedding(topic_abstract)
    if topic_embedding and memory_collection.count() > 0:
        similar_results = memory_collection.query(query_embeddings=[topic_embedding], n_results=1)
        if similar_results and similar_results['distances'] and similar_results['distances'][0] and similar_results['distances'][0][0] < SIMILARITY_THRESHOLD:
            similar_text = similar_results['documents'][0][0]
            error_msg = f"Error: El tema es demasiado similar a un tuit ya publicado sobre '{similar_text[:60]}...'. Buscando otro tema."
            print(f"❌ {error_msg}")
            return error_msg, ""

    MAX_ATTEMPTS = 3
    last_error_feedback = ""
    for attempt in range(MAX_ATTEMPTS):
        print(f"\n🚀 Intento de generación {attempt + 1}/{MAX_ATTEMPTS}...")
        try:
            with open(CONTRACT_FILE, "r", encoding="utf-8") as f:
                contract = f.read()
            feedback_prompt_addition = f"NOTE ON PREVIOUS ATTEMPT: Your last draft failed. Feedback: '{last_error_feedback}'. You MUST correct this." if last_error_feedback else ""
            prompt = f"You are a ghostwriter. Write a tweet based on the topic below, strictly following the provided contract which defines your persona. {feedback_prompt_addition}\n\n**Contract:**\n{contract}\n\n---\n**Assignment:**\n- **Topic:** {topic_abstract}\n- **Output Format:** Provide ONLY the final text in [EN] and [ES] format. BOTH must be under 280 chars."
            
            response = client.chat.completions.create(model=GENERATION_MODEL, messages=[{"role": "system", "content": "You are a world-class ghostwriter embodying the persona defined in your contract."}, {"role": "user", "content": prompt}], temperature=0.7 + (attempt * 0.05))
            raw_draft = response.choices[0].message.content.strip()
            
            eng_tweet, spa_tweet = parse_final_draft(raw_draft)
            if not eng_tweet or not spa_tweet or not is_text_in_spanish(spa_tweet, VALIDATION_MODEL):
                last_error_feedback = "Draft parsing failed or was incomplete. Ensure both [EN] and [ES] blocks are present and correct."
                continue

            eng_tweet = refine_single_tweet_style(eng_tweet, VALIDATION_MODEL, lang='en')
            spa_tweet = refine_single_tweet_style(spa_tweet, VALIDATION_MODEL, lang='es')
            
            if len(eng_tweet) > 280: eng_tweet = refine_and_shorten_tweet(eng_tweet, VALIDATION_MODEL)
            if len(spa_tweet) > 280: spa_tweet = refine_and_shorten_tweet(spa_tweet, VALIDATION_MODEL)

            if len(eng_tweet) > 280 or len(spa_tweet) > 280:
                last_error_feedback = "The generated text was too long."
                continue
            
            validation_prompt = f'You are a strict editor. Does this draft pass all rules in the ghostwriting contract (core insight, persona voice, no clichés)? Respond ONLY in JSON format: {{"pasa_validacion": boolean, "feedback_detallado": "A brief, actionable critique."}}. Draft:\n[EN] {eng_tweet}\n[ES] {spa_tweet}'
            validation_response = client.chat.completions.create(model=VALIDATION_MODEL, response_format={"type": "json_object"}, messages=[{"role": "system", "content": "You are a strict JSON editor validating text against a contract."}, {"role": "user", "content": validation_prompt}], temperature=0.0)
            validation = json.loads(validation_response.choices[0].message.content)
            
            if validation.get("pasa_validacion"):
                print(f"👍 Borrador final validado en el intento {attempt + 1}.")
                return eng_tweet, spa_tweet
            else:
                last_error_feedback = validation.get('feedback_detallado', 'No specific feedback.')
                print(f"❌ El intento {attempt + 1} no pasó la validación: {last_error_feedback}")
        
        # --- LÍNEA AÑADIDA ---
        except Exception as e:
            print(f"❌ Error crítico en el intento {attempt + 1}: {e}") # <-- ESTA LÍNEA ES LA CLAVE
            last_error_feedback = f"A critical exception occurred: {str(e)}"
    
    return "Error: No se pudo generar un borrador válido tras varios intentos.", ""

def find_relevant_topic():
    topics_collection = get_topics_collection()
    try:
        all_ids = topics_collection.get(include=[])['ids']
        if not all_ids:
            print("❌ La colección de temas está vacía.")
            return None
        random_id = random.choice(all_ids)
        topic_data = topics_collection.get(ids=[random_id])
        if topic_data and topic_data['documents']:
            topic_abstract = topic_data['documents'][0]
            topic = {"topic_id": random_id, "abstract": topic_abstract}
            print(f"✅ Tema seleccionado de la DB: '{topic_abstract[:80]}...'")
            return topic
    except Exception as e:
        print(f"❌ Error al buscar un tema en ChromaDB: {e}")
    return None

def find_topic_by_id(topic_id: str):
    topics_collection = get_topics_collection()
    try:
        topic_data = topics_collection.get(ids=[topic_id])
        if topic_data and topic_data['documents']:
            return {"topic_id": topic_id, "abstract": topic_data['documents'][0]}
    except Exception:
        pass
    
    files = [f for f in os.listdir(JSON_DIR) if f.lower().endswith(".json")]
    for file_name in files:
        filepath = os.path.join(JSON_DIR, file_name)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                for topic in data.get("extracted_topics", []):
                    if topic.get("topic_id") == topic_id:
                        return topic
        except (IOError, json.JSONDecodeError):
            continue
    return None