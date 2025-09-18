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
    eng_match = re.search(r"\[EN\s*-\s*\d+/\d+\]\s*(.*)", draft, re.DOTALL | re.IGNORECASE)
    spa_match = re.search(r"\[ES\s*-\s*\d+/\d+\]\s*(.*)", draft, re.DOTALL | re.IGNORECASE)
    english_text = eng_match.group(1).split("[ES -")[0].strip() if eng_match else ""
    spanish_text = spa_match.group(1).strip() if spa_match else ""
    return english_text, spanish_text

def generate_tweet_from_topic(topic_abstract: str):
    try:
        with open(CONTRACT_FILE, "r", encoding="utf-8") as f: contract = f.read()
        patron_elegido = random.choice(PATRONES_NIKITA)
        print(f"✍️ Escribiendo borrador (Patrón: {patron_elegido})...")
        
        for i in range(MAX_ROUNDS):
            prompt = f"Using the pattern '{patron_elegido}', write a tweet about '{topic_abstract}'. Obey the 280 character limit strictly. The contract is: {contract}"
            response = client.chat.completions.create(model=GENERATION_MODEL, messages=[{"role": "system", "content": "You are Nikita Bier's ghostwriter. You are obsessed with the 280 character limit."}, {"role": "user", "content": prompt}], temperature=0.7 + (i * 0.1))
            draft = response.choices[0].message.content.strip()
            
            eng_tweet, spa_tweet = parse_final_draft(draft)
            
            # 1. Control Matemático de Longitud
            if len(eng_tweet) > 280 or len(spa_tweet) > 280:
                print(f"⚠️ Borrador descartado por longitud ({len(eng_tweet)}/{len(spa_tweet)}). Reintentando...")
                continue

            # 2. Control de Estilo por IA
            print("🕵️ Borrador tiene longitud correcta. Validando estilo...")
            validation_prompt = f"Validate this draft: '{draft}' against the contract: {contract}. Is it under 280 chars AND does it follow the style? Respond ONLY with JSON: {{\"pasa_validacion\": boolean, \"feedback_detallado\": \"...\"}}"
            validation_response = client.chat.completions.create(model=VALIDATION_MODEL, response_format={"type": "json_object"}, messages=[{"role": "system", "content": "You are a strict JSON editor. The character limit is the most important rule."}, {"role": "user", "content": validation_prompt}], temperature=0.1)
            validation = json.loads(validation_response.choices[0].message.content)

            if validation.get("pasa_validacion"):
                print("👍 Borrador validado con éxito.")
                return eng_tweet, spa_tweet
            else:
                if i < MAX_ROUNDS - 1:
                    print(f"⚠️ Borrador no pasó la validación de estilo. Reintentando...")

        return "Error: No se pudo generar un tuit válido que cumpliera todas las reglas.", ""
    
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

def find_relevant_topic():
    files = [f for f in os.listdir(JSON_DIR) if f.lower().endswith(".json")]
    if not files: raise RuntimeError(f"No JSON files found in {JSON_DIR}")
    
    searched_files = set()
    while len(searched_files) < len(files):
        chosen_file_name = random.choice(list(set(files) - searched_files))
        searched_files.add(chosen_file_name)
        filepath = os.path.join(JSON_DIR, chosen_file_name)
        try:
            with open(filepath, "r", encoding="utf-8") as f: data = json.load(f)
        except Exception: continue
        topics = data.get("extracted_topics", [])
        if not topics: continue
        
        candidate_topic = random.choice(topics)
        
        if is_topic_coo_relevant(candidate_topic.get("abstract", "")):
            print(f"✅ Tema aprobado: {candidate_topic.get('abstract')}")
            return candidate_topic
        else:
            print(f"❌ Tema descartado: {candidate_topic.get('abstract')}")
            remove_topic_from_json(filepath, candidate_topic)
            time.sleep(1)
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