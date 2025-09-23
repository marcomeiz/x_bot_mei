import os
import json
import random
import re
import time
import tweepy
from dotenv import load_dotenv
from openai import OpenAI
import requests

# --- Carga de Configuración Completa ---
load_dotenv()
openrouter_api_key = os.getenv("OPENROUTER_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
X_API_KEY = os.getenv("X_API_KEY")
X_API_KEY_SECRET = os.getenv("X_API_KEY_SECRET")
X_ACCESS_TOKEN = os.getenv("X_ACCESS_TOKEN")
X_ACCESS_TOKEN_SECRET = os.getenv("X_ACCESS_TOKEN_SECRET")

client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=openrouter_api_key)

# --- Rutas y Constantes ---
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
CONTRACT_FILE = os.path.join(BASE_DIR, "copywriter_contract.md")
JSON_DIR = os.path.join(BASE_DIR, "json")
MAX_ROUNDS = 5
GENERATION_MODEL = "anthropic/claude-3.5-sonnet"
VALIDATION_MODEL = "anthropic/claude-3-haiku"
PATRONES_NIKITA = [
    "1. La Lección Universal", "2. El Sentimiento del Creador", "3. La Receta Contraintuitiva",
    "4. La Observación Meta", "5. La Aventura Narrativa", "6. La Comparación de Escala Hiperbólica"
]
# --- MODIFICACIÓN 1: PERSONA ALINEADA CON NIKITA ---
COO_PERSONA = "Tu persona es Nikita Bier, un COO que piensa como un constructor. Tu voz es la de un colega experimentado, no la de un gurú. El tono es directo, ingenioso y a menudo informal, pero siempre autoritativo y basado en la experiencia práctica. Prioriza la sabiduría ganada sobre la jerga corporativa."

# --- FUNCIONES AUXILIARES (INTACTAS) ---
def refine_and_shorten_tweet(tweet_text: str, model: str) -> str:
    print(f"📏 Refinando y acortando texto de {len(tweet_text)} caracteres...")
    prompt = f"""
    Your task is to shorten the following text to be under 280 characters.
    This is a hard limit. You MUST succeed.
    Do not add any extra commentary or formatting.
    Just provide the shortened text directly.
    Preserve the core message and the original tone.

    Original Text:
    "{tweet_text}"

    Shortened Text:
    """
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a ruthless text editor. Your only goal is brevity."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.4
        )
        shortened_text = response.choices[0].message.content.strip()
        print(f"✅ Texto acortado a {len(shortened_text)} caracteres.")
        return shortened_text
    except Exception as e:
        print(f"Error al acortar el texto: {e}")
        return tweet_text

def refine_single_tweet_style(raw_text: str, model: str) -> str:
    print("🎨 Refinando estilo y formato de un bloque de texto...")
    prompt = f"""
    You are a social media style editor, an expert in Nikita Bier's voice.
    Your task is to take the following dense text and rewrite it to match his specific style.
    The core insight of the text MUST remain 100% intact.

    **CRITICAL RULES:**
    1.  **Airy Formatting:** Break the text into 2-4 very short paragraphs. Use line breaks.
    2.  **Personal Voice:** Shift the tone from an academic lesson to a personal observation.
    3.  **Subtle Wit:** Add a touch of dry wit or a punchy final sentence.

    **RAW TEXT:**
    ---
    {raw_text}
    ---

    **REFINED TEXT (applying all rules):**
    """
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a world-class ghostwriter specializing in rewriting dense text into the witty, airy, and insightful style of Nikita Bier."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.6
        )
        refined_text = response.choices[0].message.content.strip()
        print("✅ Bloque de texto refinado.")
        return refined_text
    except Exception as e:
        print(f"Error al refinar el texto único: {e}")
        return raw_text

def post_tweet_to_x(text_to_post: str):
    if not all([X_API_KEY, X_API_KEY_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET]):
        print("Error: Faltan las credenciales de la API de X en el entorno.")
        return None
    try:
        client_x = tweepy.Client(
            consumer_key=X_API_KEY, consumer_secret=X_API_KEY_SECRET,
            access_token=X_ACCESS_TOKEN, access_token_secret=X_ACCESS_TOKEN_SECRET
        )
        response = client_x.create_tweet(text=text_to_post)
        print(f"Respuesta de la API de X: {response.data}")
        return response.data
    except Exception as e:
        print(f"Error al publicar en X: {e}")
        return None

def parse_final_draft(draft: str) -> (str, str):
    eng_match = re.search(r"\[EN(?:\s*-\s*\d+/\d+)?\]\s*(.*)", draft, re.DOTALL | re.IGNORECASE)
    spa_match = re.search(r"\[ES(?:\s*-\s*\d+/\d+)?\]\s*(.*)", draft, re.DOTALL | re.IGNORECASE)
    
    english_text = eng_match.group(1).split("[ES")[0].strip() if eng_match else ""
    spanish_text = spa_match.group(1).strip() if spa_match else ""
    
    return english_text, spanish_text

# --- FUNCIÓN PRINCIPAL DE GENERACIÓN (CON VALIDADOR CORREGIDO) ---
def generate_tweet_from_topic(topic_abstract: str):
    MAX_ATTEMPTS = 3
    last_error_feedback = ""

    for attempt in range(MAX_ATTEMPTS):
        print(f"\n🚀 Intento de generación {attempt + 1}/{MAX_ATTEMPTS}...")
        
        try:
            with open(CONTRACT_FILE, "r", encoding="utf-8") as f: contract = f.read()
            patron_elegido = random.choice(PATRONES_NIKITA)
            print(f"✍️ Escribiendo borrador (Patrón: {patron_elegido})...")

            feedback_prompt_addition = f"\nCRITICAL NOTE ON PREVIOUS ATTEMPT: Your last draft failed. The feedback was: '{last_error_feedback}'. You MUST correct this." if last_error_feedback else ""

            prompt = f"""
            You are Nikita Bier's ghostwriter. Your specific mindset is that of a Chief Operating Officer who thinks like a builder.
            Your persona is: "{COO_PERSONA}"

            Your task is to write a tweet based on the topic below, strictly following the provided contract.
            {feedback_prompt_addition}
            
            **Contract for style and tone:**
            {contract}

            ---
            **Your Assignment:**
            - **Topic:** {topic_abstract}
            - **Inspiration Pattern to use:** {patron_elegido}
            - **Output Format:** Provide ONLY the final text in the specified EN/ES format.
            - **HARD LENGTH CONSTRAINT:** Your response for BOTH the English and Spanish text MUST be under 280 characters each. This is a strict rule. Be concise from the start.
            """

            print("🧠 Enviando prompt de alta exigencia (Persona Nikita) a Claude 3.5 Sonnet...")
            response = client.chat.completions.create(
                model=GENERATION_MODEL,
                messages=[
                    {"role": "system", "content": "You are a world-class ghostwriter embodying the Nikita Bier persona. Your goal is authenticity through operational specificity and a direct, witty voice."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7 + (attempt * 0.05)
            )
            raw_draft = response.choices[0].message.content.strip()

            eng_tweet, spa_tweet = parse_final_draft(raw_draft)

            if not eng_tweet or not spa_tweet:
                print("Error: El borrador INICIAL no pudo ser parseado. Reintentando...")
                last_error_feedback = "The initial draft was not parsed correctly. You MUST ensure both [EN] and [ES] blocks are present and correctly formatted in your output."
                continue

            eng_tweet = refine_single_tweet_style(eng_tweet, VALIDATION_MODEL)
            spa_tweet = refine_single_tweet_style(spa_tweet, VALIDATION_MODEL)

            if len(eng_tweet) > 280:
                eng_tweet = refine_and_shorten_tweet(eng_tweet, VALIDATION_MODEL)
            if len(spa_tweet) > 280:
                spa_tweet = refine_and_shorten_tweet(spa_tweet, VALIDATION_MODEL)

            if len(eng_tweet) > 280 or len(spa_tweet) > 280:
                print(f"❌ Fallo crítico de longitud tras refinar. Reintentando...")
                last_error_feedback = "The generated text was too long. Generate a more concise draft."
                continue

            print("🕵️  Validando estilo en detalle (criterios de Nikita)...")
            
            # --- MODIFICACIÓN 2: VALIDADOR REEDUCADO ---
            validation_prompt = f"""
            You are a strict editor validating a tweet draft against Nikita Bier's ghostwriting contract.
            CRITICAL CONTEXT: The goal is to sound like Nikita, who is a COO but uses a direct, witty, and often informal tone. Your job is NOT to enforce a generic corporate COO voice. A professional but informal tone IS ALLOWED and DESIRABLE.

            Analyze the draft based on these priorities:
            1.  **Core Insight:** Is the central idea sharp, counter-intuitive, and relevant to operations/building products? (Highest Priority)
            2.  **Nikita's Voice:** Does it sound like an experienced colleague sharing a key discovery, not a corporate announcement? (High Priority)
            3.  **Contract Rules:** Does it avoid the specific clichés and "announcing" phrases from the contract?

            Provide your response ONLY in JSON format.
            **Draft:**
            [EN] {eng_tweet}
            [ES] {spa_tweet}
            **JSON Format:** {{"pasa_validacion": boolean, "feedback_detallado": "A brief, actionable critique focused ONLY on the core insight and contract rules. Mention tone only if it's completely off-brand for Nikita (e.g., sounds like marketing fluff or a generic motivational quote)."}}
            """

            validation_response = client.chat.completions.create(
                model=VALIDATION_MODEL, response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": "You are a strict JSON editor validating text against Nikita Bier's specific voice and contract rules."},
                    {"role": "user", "content": validation_prompt}
                ], temperature=0.0
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

# --- RESTO DE FUNCIONES (INTACTAS) ---
def is_topic_coo_relevant(topic_abstract: str) -> bool:
    print(f"🕵️  Validando relevancia: '{topic_abstract[:70]}...'")
    prompt = f'Is this topic "{topic_abstract}" relevant for a COO persona? Respond ONLY with JSON: {{"is_relevant": boolean}}'
    try:
        response = client.chat.completions.create(model=VALIDATION_MODEL, response_format={"type": "json_object"}, messages=[{"role": "system", "content": "You are a strict JSON validator."}, {"role": "user", "content": prompt}], temperature=0.0)
        return json.loads(response.choices[0].message.content).get("is_relevant", False)
    except Exception: return False

def remove_topic_from_json(filepath: str, topic_to_remove: dict):
    try:
        with open(filepath, "r+", encoding="utf-8") as f:
            data = json.load(f)
            data["extracted_topics"] = [t for t in data["extracted_topics"] if t.get("topic_id") != topic_to_remove.get("topic_id")]
            f.seek(0); json.dump(data, f, indent=2, ensure_ascii=False); f.truncate()
            print(f"🗑️  Tema irrelevante eliminado de {os.path.basename(filepath)}")
    except Exception as e: print(f"Error removing topic: {e}")

def find_relevant_topic():
    files = [f for f in os.listdir(JSON_DIR) if f.lower().endswith(".json")]
    if not files:
        raise RuntimeError(f"No JSON files found in {JSON_DIR}")
    
    random.shuffle(files)

    for file_name in files:
        filepath = os.path.join(JSON_DIR, file_name)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (IOError, json.JSONDecodeError):
            continue

        topics = data.get("extracted_topics", [])
        if not topics:
            continue

        random.shuffle(topics)

        for topic in topics:
            abstract = topic.get("abstract", "")
            if not abstract:
                continue

            if is_topic_coo_relevant(abstract):
                print(f"✅ Tema aprobado de '{file_name}': {abstract}")
                return topic
            else:
                print(f"❌ Tema descartado de '{file_name}': {abstract}")
                remove_topic_from_json(filepath, topic)
                time.sleep(1)

    print("⚠️ No se encontraron temas relevantes en ningún fichero JSON.")
    return None

def find_topic_by_id(topic_id: str):
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