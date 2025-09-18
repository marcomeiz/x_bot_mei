import os
import json
import random
import re
import time
from dotenv import load_dotenv
from openai import OpenAI
import requests

# --- Carga de Configuración ---
load_dotenv()
openrouter_api_key = os.getenv("OPENROUTER_API_KEY")
# ... (el resto de tus variables de entorno)

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=openrouter_api_key,
)

# --- MODIFICACIÓN: Construcción de Rutas Absolutas ---
# Obtiene la ruta del directorio donde se encuentra este script
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
# Construye las rutas completas a los archivos y carpetas
CONTRACT_FILE = os.path.join(BASE_DIR, "copywriter_contract.md")
JSON_DIR = os.path.join(BASE_DIR, "json")

# --- Constantes y Definiciones ---
MAX_ROUNDS = 5
GENERATION_MODEL = "anthropic/claude-3.5-sonnet"
VALIDATION_MODEL = "anthropic/claude-3-haiku"

PATRONES_NIKITA = [
    "1. La Lección Universal", "2. El Sentimiento del Creador", "3. La Receta Contraintuitiva",
    "4. La Observación Meta", "5. La Aventura Narrativa"
]

COO_PERSONA = """
Un Chief Operating Officer (COO) enfocado en liderazgo operacional, ejecución,
escalado de negocios, sistemas, procesos, gestión de equipos de alto rendimiento,
productividad y la intersección entre estrategia y operaciones del día a día.
"""

# ... (El resto de tus funciones: is_topic_coo_relevant, remove_topic_from_json, etc. permanecen igual) ...
def is_topic_coo_relevant(topic_abstract: str) -> bool:
    prompt = f'Is this topic "{topic_abstract}" relevant for a COO persona focused on operations, execution, and leadership? Respond ONLY with JSON: {{"is_relevant": boolean}}'
    try:
        response = client.chat.completions.create(model=VALIDATION_MODEL, response_format={"type": "json_object"}, messages=[{"role": "system", "content": "You are a strict JSON validator."}, {"role": "user", "content": prompt}], temperature=0.0)
        return json.loads(response.choices[0].message.content).get("is_relevant", False)
    except Exception: return False

def remove_topic_from_json(filepath: str, topic_to_remove: dict):
    try:
        with open(filepath, "r+", encoding="utf-8") as f:
            data = json.load(f)
            original_count = len(data["extracted_topics"])
            data["extracted_topics"] = [t for t in data["extracted_topics"] if t.get("topic_id") != topic_to_remove.get("topic_id")]
            if len(data["extracted_topics"]) < original_count:
                f.seek(0); json.dump(data, f, indent=2, ensure_ascii=False); f.truncate()
    except Exception as e: print(f"Error removing topic: {e}")

def find_and_validate_topic() -> (dict, str):
    files = [f for f in os.listdir(JSON_DIR) if f.lower().endswith(".json")]
    if not files: raise RuntimeError("No JSON files found.")
    while True:
        filepath = os.path.join(JSON_DIR, random.choice(files))
        with open(filepath, "r", encoding="utf-8") as f: data = json.load(f)
        topics = data.get("extracted_topics", [])
        if not topics: continue
        candidate_topic = random.choice(topics)
        if is_topic_coo_relevant(candidate_topic.get("abstract", "")):
            print(f"Topic approved: {candidate_topic.get('abstract')}")
            return candidate_topic, filepath
        else:
            print(f"Topic discarded: {candidate_topic.get('abstract')}")
            remove_topic_from_json(filepath, candidate_topic)

def generate_initial_draft(topic: str, contract: str, pattern: str) -> str:
    prompt = f"Using the pattern '{pattern}', write a tweet about '{topic}'. Obey the contract: {contract}"
    response = client.chat.completions.create(model=GENERATION_MODEL, messages=[{"role": "system", "content": "You are Nikita Bier's ghostwriter."}, {"role": "user", "content": prompt}], temperature=0.7)
    return response.choices[0].message.content.strip()

def validate_with_ai(draft: str, contract: str) -> dict:
    prompt = f"Validate this draft: '{draft}' against the contract: {contract}. Respond ONLY with JSON: {{\"pasa_validacion\": boolean, \"feedback_detallado\": \"...\"}}"
    try:
        response = client.chat.completions.create(model=VALIDATION_MODEL, response_format={"type": "json_object"}, messages=[{"role": "system", "content": "You are a strict JSON editor."}, {"role": "user", "content": prompt}], temperature=0.1)
        return json.loads(response.choices[0].message.content)
    except Exception: return {"pasa_validacion": False, "feedback_detallado": "AI validation failed."}
    
def parse_final_draft(draft: str) -> (str, str):
    eng_match = re.search(r"\[EN\s*-\s*\d+/\d+\]\s*(.*)", draft, re.DOTALL | re.IGNORECASE)
    spa_match = re.search(r"\[ES\s*-\s*\d+/\d+\]\s*(.*)", draft, re.DOTALL | re.IGNORECASE)
    english_text = eng_match.group(1).split("[ES -")[0].strip() if eng_match else ""
    spanish_text = spa_match.group(1).strip() if spa_match else ""
    return english_text, spanish_text

def generate_single_tweet():
    """
    Realiza todo el proceso de generar un tuit validado y devuelve el resultado.
    Siempre devuelve una tupla de dos valores (string, string).
    """
    try:
        contract = open(CONTRACT_FILE, "r", encoding="utf-8").read()
        topic_data, _ = find_and_validate_topic()
        topic_abstract = topic_data.get("abstract")

        if not topic_abstract:
            return "Error: No se pudo encontrar un tema válido.", ""

        patron_elegido = random.choice(PATRONES_NIKITA)
        draft = ""

        for _ in range(MAX_ROUNDS):
            if draft == "":
                draft = generate_initial_draft(topic_abstract, contract, patron_elegido)
            else:
                pass 

            validation = validate_with_ai(draft, contract)
            
            if validation.get("pasa_validacion"):
                eng_tweet, spa_tweet = parse_final_draft(draft)
                return eng_tweet, spa_tweet
        
        return "Error: No se pudo generar un tuit válido después de varios intentos.", ""

    except Exception as e:
        print(f"Error crítico en generate_single_tweet: {e}")
        return f"Error crítico durante la generación: {e}", ""