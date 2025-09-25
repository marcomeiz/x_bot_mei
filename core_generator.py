import os
import json
import random
import re
import time
from dotenv import load_dotenv
from openai import OpenAI

# Añade esta línea cerca de los otros imports
from embeddings_manager import get_embedding, topics_collection, memory_collection

# --- Carga de Configuración Completa ---
load_dotenv()
openrouter_api_key = os.getenv("OPENROUTER_API_KEY")

client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=openrouter_api_key)

# --- Rutas y Constantes ---
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
CONTRACT_FILE = os.path.join(BASE_DIR, "copywriter_contract.md")
JSON_DIR = os.path.join(BASE_DIR, "json")
GENERATION_MODEL = "anthropic/claude-3.5-sonnet"
VALIDATION_MODEL = "anthropic/claude-3-haiku"
SIMILARITY_THRESHOLD = 0.25 # Umbral de similitud. Si es más bajo, es "demasiado parecido".

# --- FUNCIONES AUXILIARES ---
def refine_and_shorten_tweet(tweet_text: str, model: str) -> str:
    print(f"📏 Refinando y acortando texto de {len(tweet_text)} caracteres...")
    prompt = f"""
    Your task is to shorten the following text to be under 280 characters.
    This is a hard limit. You MUST succeed.
    Do not add any extra commentary or formatting.
    Just provide the shortened text directly.
    Preserve the core message and the original tone.
    Original Text: "{tweet_text}"
    Shortened Text:
    """
    try:
        response = client.chat.completions.create(
            model=model, messages=[{"role": "system", "content": "You are a ruthless text editor. Your only goal is brevity."}, {"role": "user", "content": prompt}], temperature=0.4
        )
        shortened_text = response.choices[0].message.content.strip()
        print(f"✅ Texto acortado a {len(shortened_text)} caracteres.")
        return shortened_text
    except Exception as e:
        print(f"Error al acortar el texto: {e}")
        return tweet_text

def refine_single_tweet_style(raw_text: str, model: str, lang: str = 'en') -> str:
    print(f"🎨 Refinando estilo y formato de un bloque de texto en '{lang}'...")

    if lang == 'es':
        prompt = f"""
        Eres un editor de estilo experto. Tu tarea es tomar el siguiente texto denso y reescribirlo para que coincida con el estilo definido en un contrato (formato aéreo, voz personal, ingenio).
        El insight principal del texto DEBE permanecer 100% intacto.
        **REGLAS CRÍTICAS:**
        1.  **Formato Aéreo:** Divide el texto en 2-4 párrafos muy cortos. Usa saltos de línea.
        2.  **Voz Personal:** Cambia el tono de una lección académica a una observación personal.
        3.  **Ingenio Sutil:** Añade un toque de ingenio seco o una frase final contundente.
        **TEXTO EN BRUTO:** --- {raw_text} ---
        **TEXTO REFINADO (aplicando todas las reglas):**
        """
        system_message = "Eres un ghostwriter de clase mundial especializado en reescribir texto denso en el estilo ingenioso, aéreo y perspicaz definido en un contrato, adaptado al español."
    else: # Default a Inglés
        prompt = f"""
        You are a social media style editor, an expert in a specific persona's voice defined by a contract.
        Your task is to take the following dense text and rewrite it to match that specific style (airy, personal, witty).
        The core insight of the text MUST remain 100% intact.
        **CRITICAL RULES:**
        1.  **Airy Formatting:** Break the text into 2-4 very short paragraphs. Use line breaks.
        2.  **Personal Voice:** Shift the tone from an academic lesson to a personal observation.
        3.  **Subtle Wit:** Add a touch of dry wit or a punchy final sentence.
        **RAW TEXT:** --- {raw_text} ---
        **REFINED TEXT (applying all rules):**
        """
        system_message = "You are a world-class ghostwriter specializing in rewriting dense text into a witty, airy, and insightful style defined by a contract."

    try:
        response = client.chat.completions.create(
            model=model, messages=[{"role": "system", "content": system_message}, {"role": "user", "content": prompt}], temperature=0.6
        )
        refined_text = response.choices[0].message.content.strip()
        print("✅ Bloque de texto refinado.")
        return refined_text
    except Exception as e:
        print(f"Error al refinar el texto único: {e}")
        return raw_text

def parse_final_draft(draft: str) -> (str, str):
    eng_match = re.search(r"\[EN(?:\s*-\s*\d+/\d+)?\]\s*(.*)", draft, re.DOTALL | re-IGNORECASE)
    spa_match = re.search(r"\[ES(?:\s*-\s*\d+/\d+)?\]\s*(.*)", draft, re.DOTALL | re-IGNORECASE)
    english_text = eng_match.group(1).split("[ES")[0].strip() if eng_match else ""
    spanish_text = spa_match.group(1).strip() if spa_match else ""
    return english_text, spanish_text

def is_text_in_spanish(text: str, model: str) -> bool:
    print("🇪🇸  Verificando que el texto esté en español...")
    prompt = f"""
    Is the following text written in Spanish?
    Respond ONLY with JSON in the format: {{"is_spanish": boolean}}
    Text to analyze: --- {text} ---
    """
    try:
        response = client.chat.completions.create(
            model=model, response_format={"type": "json_object"}, messages=[{"role": "system", "content": "You are a language identification expert that only responds in JSON."}, {"role": "user", "content": prompt}], temperature=0.0
        )
        result = json.loads(response.choices[0].message.content)
        return result.get("is_spanish", False)
    except Exception as e:
        print(f"Error al verificar el idioma: {e}")
        return False

# --- FUNCIÓN PRINCIPAL DE GENERACIÓN (MODIFICADA) ---
def generate_tweet_from_topic(topic_abstract: str):
    # --- NUEVO: CHEQUEO DE MEMORIA ANTI-REPETICIÓN ---
    print(f"🧠 Comprobando si el tema '{topic_abstract[:50]}...' es repetitivo...")
    topic_embedding = get_embedding(topic_abstract)
    if topic_embedding and memory_collection.count() > 0:
        similar_results = memory_collection.query(
            query_embeddings=[topic_embedding],
            n_results=1
        )
        # La distancia en ChromaDB con 'cosine' va de 0 (idéntico) a 2 (opuesto).
        # Un valor bajo significa muy similar.
        if similar_results and similar_results['distances'][0][0] < SIMILARITY_THRESHOLD:
            similar_text = similar_results['documents'][0][0]
            error_msg = f"Error: El tema es demasiado similar a un tuit ya publicado sobre '{similar_text[:60]}...'. Buscando otro tema."
            print(f"❌ {error_msg}")
            return error_msg, ""

    MAX_ATTEMPTS = 3
    last_error_feedback = ""
    for attempt in range(MAX_ATTEMPTS):
        print(f"\n🚀 Intento de generación {attempt + 1}/{MAX_ATTEMPTS}...")
        try:
            with open(CONTRACT_FILE, "r", encoding="utf-8") as f: contract = f.read()
            
            feedback_prompt_addition = f"\nCRITICAL NOTE ON PREVIOUS ATTEMPT: Your last draft failed. The feedback was: '{last_error_feedback}'. You MUST correct this." if last_error_feedback else ""
            prompt = f"""
            You are a world-class ghostwriter. Your task is to write a tweet based on the topic below, strictly following the provided contract which defines your persona and voice.
            {feedback_prompt_addition}
            **Contract for style and tone:**
            {contract}
            ---
            **Assignment:**
            - **Topic:** {topic_abstract}
            - **Output Format:** Provide ONLY the final text in the specified EN/ES format.
            - **HARD LENGTH CONSTRAINT:** Your response for BOTH the English and Spanish text MUST be under 280 characters each. This is a strict rule. Be concise from the start.
            """
            print("🧠 Enviando prompt de alta exigencia a Claude 3.5 Sonnet...")
            response = client.chat.completions.create(
                model=GENERATION_MODEL, messages=[{"role": "system", "content": "You are a world-class ghostwriter embodying the persona defined in your contract. Your goal is authenticity through operational specificity and a direct, witty voice."}, {"role": "user", "content": prompt}], temperature=0.7 + (attempt * 0.05)
            )
            raw_draft = response.choices[0].message.content.strip()
            eng_tweet, spa_tweet = parse_final_draft(raw_draft)

            if not eng_tweet or not spa_tweet or not is_text_in_spanish(spa_tweet, VALIDATION_MODEL):
                last_error_feedback = "The initial draft was not parsed correctly, was missing a language, or the ES version was not in Spanish. You MUST ensure both [EN] and [ES] blocks are present and correct."
                continue

            eng_tweet = refine_single_tweet_style(eng_tweet, VALIDATION_MODEL, lang='en')
            spa_tweet = refine_single_tweet_style(spa_tweet, VALIDATION_MODEL, lang='es')

            if len(eng_tweet) > 280: eng_tweet = refine_and_shorten_tweet(eng_tweet, VALIDATION_MODEL)
            if len(spa_tweet) > 280: spa_tweet = refine_and_shorten_tweet(spa_tweet, VALIDATION_MODEL)

            if len(eng_tweet) > 280 or len(spa_tweet) > 280:
                last_error_feedback = "The generated text was too long. Generate a more concise draft."
                continue

            print("🕵️  Validando estilo en detalle...")
            validation_prompt = f"""
            You are a strict editor validating a tweet draft against a ghostwriting contract.
            CRITICAL CONTEXT: The persona is a COO, but the desired tone is direct, personal, and witty, NOT a generic corporate voice. Your job is to enforce the rules of the provided contract.
            Analyze the draft based on these priorities:
            1.  **Core Insight:** Is the central idea sharp and relevant to operations/building?
            2.  **Persona's Voice:** Does it sound like an experienced colleague sharing a discovery, not a corporate announcement?
            3.  **Contract Rules:** Does it avoid the specific clichés and "announcing" phrases from the contract?
            Provide your response ONLY in JSON format.
            **Draft:**
            [EN] {eng_tweet}
            [ES] {spa_tweet}
            **JSON Format:** {{"pasa_validacion": boolean, "feedback_detallado": "A brief, actionable critique focused ONLY on the core insight and contract rules."}}
            """
            validation_response = client.chat.completions.create(
                model=VALIDATION_MODEL, response_format={"type": "json_object"}, messages=[{"role": "system", "content": "You are a strict JSON editor validating text against a specific ghostwriting contract and persona."}, {"role": "user", "content": validation_prompt}], temperature=0.0
            )
            validation = json.loads(validation_response.choices[0].message.content)

            if validation.get("pasa_validacion"):
                print(f"👍 Borrador final validado con éxito en el intento {attempt + 1}.")
                return eng_tweet, spa_tweet
            else:
                last_error_feedback = validation.get('feedback_detallado', 'No specific feedback provided.')
                print(f"❌ El intento {attempt + 1} no pasó la validación: {last_error_feedback}")
        except Exception as e:
            print(f"Error crítico en el intento {attempt + 1}: {e}")
            last_error_feedback = f"A critical exception occurred: {str(e)}"
    print("🚨 Se agotaron todos los intentos. No se pudo generar un borrador válido.")
    return "Error: No se pudo generar un borrador válido tras varios intentos.", ""

# --- FUNCIÓN DE SELECCIÓN DE TEMAS (MODIFICADA) ---
def find_relevant_topic():
    """
    Encuentra un tema relevante usando la base de datos de embeddings.
    Elige un tema al azar de toda la colección para garantizar la variedad.
    """
    try:
        all_ids = topics_collection.get(include=[])['ids']
        if not all_ids:
            print("❌ La colección de temas está vacía.")
            return None
            
        random_id = random.choice(all_ids)
        topic_data = topics_collection.get(ids=[random_id])
        
        if topic_data and topic_data['documents']:
            topic_abstract = topic_data['documents'][0]
            topic = { "topic_id": random_id, "abstract": topic_abstract }
            print(f"✅ Tema seleccionado de la DB: '{topic_abstract[:80]}...'")
            return topic
    except Exception as e:
        print(f"❌ Error al buscar un tema en ChromaDB: {e}")
    return None

def find_topic_by_id(topic_id: str):
    try:
        topic_data = topics_collection.get(ids=[topic_id])
        if topic_data and topic_data['documents']:
            return { "topic_id": topic_id, "abstract": topic_data['documents'][0] }
    except Exception:
        pass
    # Fallback to JSON files if not found in ChromaDB (for older topics)
    files = [f for f in os.listdir(JSON_DIR) if f.lower().endswith(".json")]
    for file_name in files:
        filepath = os.path.join(JSON_DIR, file_name)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                for topic in data.get("extracted_topics", []):
                    if topic.get("topic_id") == topic_id:
                        return topic
        except (IOError, json.JSONDecodeError): continue
    return None