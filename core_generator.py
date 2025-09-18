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
COO_PERSONA = "Un Chief Operating Officer (COO) enfocado en liderazgo operacional, ejecución, escalado de negocios, sistemas y procesos."

# --- FUNCIÓN NUEVA PARA ACORTAR ---
def refine_and_shorten_tweet(tweet_text: str, model: str) -> str:
    """
    Toma un texto y usa un LLM para acortarlo a menos de 280 caracteres,
    preservando el mensaje central.
    """
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
            temperature=0.4 # Una temperatura más baja para ser más directo
        )
        shortened_text = response.choices[0].message.content.strip()
        print(f"✅ Texto acortado a {len(shortened_text)} caracteres.")
        return shortened_text
    except Exception as e:
        print(f"Error al acortar el texto: {e}")
        return tweet_text # Devuelve el original si falla

# --- FUNCIÓN PARA PUBLICAR EN X ---
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

# --- RESTO DE FUNCIONES DE CORE_GENERATOR ---

def parse_final_draft(draft: str) -> (str, str):
    # --- MODIFICACIÓN 2: REGEX MÁS FLEXIBLE ---
    # (?: ... )? crea un grupo opcional que no se captura.
    # Ahora aceptará "[EN - 123/280]" y también "[EN]"
    eng_match = re.search(r"\[EN(?:\s*-\s*\d+/\d+)?\]\s*(.*)", draft, re.DOTALL | re.IGNORECASE)
    spa_match = re.search(r"\[ES(?:\s*-\s*\d+/\d+)?\]\s*(.*)", draft, re.DOTALL | re.IGNORECASE)
    # --- FIN DE LA MODIFICACIÓN 2 ---
    
    english_text = eng_match.group(1).split("[ES")[0].strip() if eng_match else ""
    spanish_text = spa_match.group(1).strip() if spa_match else ""
    
    return english_text, spanish_text

def generate_tweet_from_topic(topic_abstract: str):
    try:
        with open(CONTRACT_FILE, "r", encoding="utf-8") as f: contract = f.read()
        patron_elegido = random.choice(PATRONES_NIKITA)
        print(f"✍️ Escribiendo borrador (Patrón: {patron_elegido})...")
        
        # --- MODIFICACIÓN CLAVE 1: PROMPT DE GENERACIÓN MÁS LIBRE ---
        # Eliminamos la regla CRITICAL SUPREME RULE para darle libertad creativa.
        prompt = f"""
        Your task is to write a tweet in two languages (English and Spanish) about the following topic.
        Follow the contract provided to nail the style and tone.

        **Pattern to use:** {patron_elegido}
        **Topic:** {topic_abstract}

        **Contract for style reference:**
        {contract}
        """

        # Hacemos un solo intento de alta calidad. El bucle ya no es tan necesario aquí.
        response = client.chat.completions.create(
            model=GENERATION_MODEL, 
            messages=[
                {"role": "system", "content": "You are a creative ghostwriter embodying the persona in the contract."},
                {"role": "user", "content": prompt}
            ], 
            temperature=0.75
        )
        draft = response.choices[0].message.content.strip()
        
        eng_tweet, spa_tweet = parse_final_draft(draft)

        if not eng_tweet or not spa_tweet:
            print("Error: El borrador inicial no pudo ser parseado. No se encontraron los bloques [EN] o [ES].")
            return "Error: Formato de borrador inicial inválido.", ""

        # --- MODIFICACIÓN CLAVE 2: REFINAR SI ES NECESARIO ---
        # En lugar de descartar, acortamos programáticamente.
        if len(eng_tweet) > 280:
            print(f"⚠️ Borrador EN demasiado largo ({len(eng_tweet)}/280). Enviando a refinar...")
            eng_tweet = refine_and_shorten_tweet(eng_tweet, VALIDATION_MODEL) # Usamos Haiku que es más rápido y barato
        
        if len(spa_tweet) > 280:
            print(f"⚠️ Borrador ES demasiado largo ({len(spa_tweet)}/280). Enviando a refinar...")
            spa_tweet = refine_and_shorten_tweet(spa_tweet, VALIDATION_MODEL)

        # Verificación final después del refinamiento
        if len(eng_tweet) > 280 or len(spa_tweet) > 280:
            print(f"❌ Fallo crítico: Incluso después de refinar, el tuit excede los 280 caracteres ({len(eng_tweet)}/{len(spa_tweet)}).")
            return "Error: No se pudo acortar el tuit lo suficiente.", ""
            
        # La validación de estilo sigue siendo una buena idea
        print("🕵️ Borrador con longitud correcta. Validando estilo...")
        validation_prompt = f"Validate this draft: '{eng_tweet}\n\n{spa_tweet}' against the contract. Does it follow the style? Respond ONLY with JSON: {{\"pasa_validacion\": boolean, \"feedback_detallado\": \"...\"}}"
        validation_response = client.chat.completions.create(
            model=VALIDATION_MODEL, 
            response_format={"type": "json_object"}, 
            messages=[
                {"role": "system", "content": "You are a strict JSON editor focused on style and tone."}, 
                {"role": "user", "content": validation_prompt}
            ], 
            temperature=0.1
        )
        validation = json.loads(validation_response.choices[0].message.content)

        if validation.get("pasa_validacion"):
            print("👍 Borrador final validado con éxito.")
            return eng_tweet, spa_tweet
        else:
            print(f"⚠️ El borrador final no pasó la validación de estilo: {validation.get('feedback_detallado')}")
            # Aún así, devolvemos el borrador porque ya cumple la longitud, que era el problema principal.
            # Podrías decidir devolver un error si el estilo es crítico.
            return eng_tweet, spa_tweet

    except Exception as e:
        print(f"Error crítico en generate_tweet_from_topic: {e}")
        return f"Error crítico durante la generación: {e}", ""

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

# --- MODIFICACIÓN 3: BÚSQUEDA DE TEMAS OPTIMIZADA ---
def find_relevant_topic():
    files = [f for f in os.listdir(JSON_DIR) if f.lower().endswith(".json")]
    if not files:
        raise RuntimeError(f"No JSON files found in {JSON_DIR}")
    
    random.shuffle(files) # 1. Barajamos los ficheros una sola vez

    for file_name in files:
        filepath = os.path.join(JSON_DIR, file_name)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (IOError, json.JSONDecodeError):
            continue # Si el fichero está corrupto o no se puede leer, saltamos al siguiente

        topics = data.get("extracted_topics", [])
        if not topics:
            continue

        random.shuffle(topics) # 2. Barajamos los temas dentro del fichero

        for topic in topics:
            abstract = topic.get("abstract", "")
            if not abstract:
                continue

            if is_topic_coo_relevant(abstract):
                print(f"✅ Tema aprobado de '{file_name}': {abstract}")
                return topic # 3. Encontramos uno, lo devolvemos y la función termina
            else:
                print(f"❌ Tema descartado de '{file_name}': {abstract}")
                remove_topic_from_json(filepath, topic)
                time.sleep(1) # Pequeña pausa para no sobrecargar el disco

    print("⚠️ No se encontraron temas relevantes en ningún fichero JSON.")
    return None # Si recorremos todo y no encontramos nada, devolvemos None
# --- FIN DE LA MODIFICACIÓN 3 ---

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