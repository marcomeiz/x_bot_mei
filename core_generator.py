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

# --- FUNCIÓN NUEVA PARA APLICAR ESTILO Y FORMATO ---
def refine_tweet_style(raw_draft: str, model: str) -> str:
    """
    Toma un borrador en bruto y le aplica el estilo, formato y voz de Nikita Bier.
    """
    print("🎨 Refinando estilo y formato del borrador...")
    prompt = f"""
    You are a social media style editor, an expert in Nikita Bier's voice.
    Your task is to take a raw, dense draft and rewrite it to match his specific style.
    The core insight of the draft MUST remain 100% intact.

    **CRITICAL RULES:**
    1.  **Airy Formatting:** Break the text into 2-4 very short paragraphs. Use line breaks. Dense text blocks are forbidden.
    2.  **Personal Voice:** Shift the tone from an academic lesson to a personal observation shared with a colleague. It should feel like earned wisdom, not a lecture.
    3.  **Subtle Wit:** If possible, add a touch of dry wit or a punchy final sentence.
    4.  **Preserve the Core:** Do not alter the central counter-intuitive insight of the original draft.

    **RAW DRAFT:**
    ---
    {raw_draft}
    ---

    **REFINED DRAFT (applying all rules):**
    """
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a world-class ghostwriter specializing in rewriting dense text into the witty, airy, and insightful style of Nikita Bier."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.6 # Un poco de creatividad para el estilo
        )
        refined_text = response.choices[0].message.content.strip()
        print("✅ Estilo y formato refinados.")
        return refined_text
    except Exception as e:
        print(f"Error al refinar el estilo: {e}")
        return raw_draft # Devuelve el original si falla

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

# COPIA Y PEGA ESTA FUNCIÓN COMPLETA EN LUGAR DE LA TUYA EN core_generator.py

def generate_tweet_from_topic(topic_abstract: str):
    MAX_ATTEMPTS = 3  # Definimos un número máximo de intentos para la autocorrección
    last_error_feedback = "" # Variable para guardar el feedback entre intentos

    # Inicia el bucle de autocorrección
    for attempt in range(MAX_ATTEMPTS):
        print(f"\n🚀 Intento de generación {attempt + 1}/{MAX_ATTEMPTS}...")
        
        try:
            # --- Tu lógica de generación original, ahora dentro del bucle ---
            with open(CONTRACT_FILE, "r", encoding="utf-8") as f: contract = f.read()
            patron_elegido = random.choice(PATRONES_NIKITA)
            print(f"✍️ Escribiendo borrador (Patrón: {patron_elegido})...")

            # MEJORA: Añadimos el feedback del error anterior para que la IA aprenda
            feedback_prompt_addition = f"\nCRITICAL NOTE ON PREVIOUS ATTEMPT: Your last draft failed validation. The feedback was: '{last_error_feedback}'. You MUST correct this in the new draft. Do not repeat the same mistake." if last_error_feedback else ""

            prompt = f"""
            You are Nikita Bier's ghostwriter. Your specific mindset is that of a Chief Operating Officer.
            Your persona is: "{COO_PERSONA}"

            Your task is to write a tweet based on the topic below, strictly following the provided contract.
            {feedback_prompt_addition}
            
            **Contract for style and tone:**
            {contract}

            ---
            **CRITICAL SUPREME RULE: PRE-GENERATION CHECKLIST**
            Before writing a single word, you MUST mentally confirm the following:
            1.  **Mindset Check:** Am I writing from the perspective of a Chief Operating Officer? My focus must be on operational leadership, execution, scaling, systems, and processes. (COO Mindset)
            2.  **Specificity Check:** Is my core idea grounded in a specific, tangible detail from an operational scenario, not a grand, abstract metaphor? (Cláusula Anti-Cliché)
            3.  **Cliché Check:** Have I identified and avoided all business/tech clichés like "game-changer", "synergy", "company DNA"?
            4.  **Subtlety Check:** Am I SHOWING the operational insight directly instead of ANNOUNCING it with phrases like "Here's an important lesson:"? (Regla de Sutileza)
            5.  **Opening Check:** Is my opening sentence varied and engaging? I will avoid starting with "Most COOs think..." or a similar formula. (Cláusula de Aperturas Variadas)

            Only after you have confirmed these five points, proceed with writing the tweet.

            ---
            **Your Assignment:**
            - **Topic:** {topic_abstract}
            - **Inspiration Pattern to use:** {patron_elegido}
            - **Output Format:** Provide ONLY the final text in the specified EN/ES format. Do not add commentary.
            """

            print("🧠 Enviando prompt de alta exigencia (COO Persona) a Claude 3.5 Sonnet...")
            response = client.chat.completions.create(
                model=GENERATION_MODEL,
                messages=[
                    {"role": "system", "content": "You are a world-class ghostwriter embodying the COO persona defined in the user's contract. Your primary goal is authenticity through operational specificity."},
                    {"role": "user", "content": prompt}
                ],
                # MEJORA: Aumentamos la temperatura en cada reintento para evitar respuestas idénticas
                temperature=0.7 + (attempt * 0.05)
            )
            draft = response.choices[0].message.content.strip()
            draft = refine_tweet_style(draft, VALIDATION_MODEL)
            eng_tweet, spa_tweet = parse_final_draft(draft)

            if not eng_tweet or not spa_tweet:
                print("Error: El borrador no pudo ser parseado. Reintentando...")
                last_error_feedback = "The draft was not parsed correctly. Ensure both [EN] and [ES] blocks are present and correctly formatted."
                continue # Pasa al siguiente intento

            if len(eng_tweet) > 280:
                print(f"⚠️ Borrador EN demasiado largo ({len(eng_tweet)}/280). Enviando a refinar...")
                eng_tweet = refine_and_shorten_tweet(eng_tweet, VALIDATION_MODEL)

            if len(spa_tweet) > 280:
                print(f"⚠️ Borrador ES demasiado largo ({len(spa_tweet)}/280). Enviando a refinar...")
                spa_tweet = refine_and_shorten_tweet(spa_tweet, VALIDATION_MODEL)

            if len(eng_tweet) > 280 or len(spa_tweet) > 280:
                print(f"❌ Fallo crítico: Incluso después de refinar, el tuit excede los 280 caracteres. Reintentando...")
                last_error_feedback = "The generated text was too long and could not be shortened sufficiently. Please generate a more concise draft from the beginning."
                continue # Pasa al siguiente intento

            print("🕵️ Borrador con longitud correcta. Validando estilo en detalle...")
            validation_prompt = f"""
            Analyze the following tweet draft against the ghostwriter contract, specifically from a COO's perspective.
            Provide your response ONLY in JSON format.
            **Draft:**
            [EN] {eng_tweet}
            [ES] {spa_tweet}
            **JSON Response format:**
            {{
                "pasa_validacion": boolean, "violates_anti_cliche_clause": boolean, "cliches_found": ["list", "of", "cliches"],
                "violates_subtlety_clause": boolean, "announcing_phrase_found": "The phrase it used",
                "feedback_detallado": "A brief, actionable critique on why it fails or passes the COO persona test."
            }}
            """
            validation_response = client.chat.completions.create(
                model=VALIDATION_MODEL, response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": "You are a strict JSON editor focused on validating text against a contract's anti-cliché and subtlety rules from a COO's perspective."},
                    {"role": "user", "content": validation_prompt}
                ], temperature=0.0
            )
            validation = json.loads(validation_response.choices[0].message.content)

            # --- LA LÓGICA DE DECISIÓN CORREGIDA ---
            if validation.get("pasa_validacion"):
                print(f"👍 Borrador final validado con éxito en el intento {attempt + 1}.")
                return eng_tweet, spa_tweet # ¡ÉXITO! La función termina y devuelve el tuit bueno.
            else:
                # FALLO: Guardamos el feedback y dejamos que el bucle continúe al siguiente intento.
                last_error_feedback = validation.get('feedback_detallado', 'No specific feedback provided.')
                print(f"❌ El intento {attempt + 1} no pasó la validación detallada.")
                print(f"   Feedback: {last_error_feedback}")
                print(f"   Clichés encontrados: {validation.get('cliches_found')}")
                # El bucle continuará automáticamente a la siguiente iteración.

        except Exception as e:
            print(f"Error crítico en el intento {attempt + 1}: {e}")
            last_error_feedback = f"A critical exception occurred: {str(e)}"
            # El bucle continuará al siguiente intento.

    # --- GESTIÓN DE FALLO TOTAL ---
    # Si el bucle termina (es decir, se agotaron los 3 intentos), devolvemos un error definitivo.
    print("🚨 Se agotaron todos los intentos. No se pudo generar un borrador válido.")
    return "Error: No se pudo generar un borrador válido tras varios intentos.", ""

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