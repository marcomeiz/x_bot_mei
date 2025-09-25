# bot.py
import os
import json
import threading
from dotenv import load_dotenv
from flask import Flask, request
import requests
from urllib.parse import quote_plus

from embeddings_manager import get_embedding, get_memory_collection
from core_generator import find_relevant_topic, generate_tweet_from_topic, find_topic_by_id

load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
app = Flask(__name__)
TEMP_DIR = "/tmp"

# --- (get_new_tweet_keyboard, send_telegram_message, edit_telegram_message no cambian) ---
def get_new_tweet_keyboard():
    return {"inline_keyboard": [[{"text": "🚀 Generar Nuevo Tuit", "callback_data": "generate_new"}]]}

def send_telegram_message(chat_id, text, reply_markup=None):
    # ... (contenido sin cambios) ...
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup: payload["reply_markup"] = reply_markup
    try: requests.post(url, json=payload)
    except Exception as e: print(f"Error enviando mensaje: {e}")

def edit_telegram_message(chat_id, message_id, text, reply_markup=None):
    # ... (contenido sin cambios) ...
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "Markdown"}
    requests.post(url, json=payload)

# --- (do_the_work no cambia) ---
def do_the_work(chat_id):
    # ... (contenido sin cambios) ...
    max_retries = 5
    for _ in range(max_retries):
        topic = find_relevant_topic()
        if topic:
            if propose_tweet(chat_id, topic): return
            send_telegram_message(chat_id, "⚠️ Tema descartado por ser muy similar a uno anterior. Buscando otro...")
        else:
            send_telegram_message(chat_id, "❌ No pude encontrar un tema relevante.", reply_markup=get_new_tweet_keyboard())
            return
    send_telegram_message(chat_id, "❌ No pude encontrar un tema único tras varios intentos.", reply_markup=get_new_tweet_keyboard())

# --- FUNCIÓN DE PROPUESTA MODIFICADA ---
def propose_tweet(chat_id, topic):
    topic_abstract = topic.get("abstract")
    topic_id = topic.get("topic_id")
    send_telegram_message(chat_id, f"✍️ Tema: '{topic_abstract[:80]}...'.\nGenerando 2 alternativas...")
    
    draft_a, draft_b = generate_tweet_from_topic(topic_abstract)
    
    if "Error: El tema es demasiado similar" in draft_a: return False
    if "Error:" in draft_a:
        send_telegram_message(chat_id, f"Hubo un problema: {draft_a}", reply_markup=get_new_tweet_keyboard())
        return True

    temp_file_path = os.path.join(TEMP_DIR, f"{chat_id}_{topic_id}.tmp")
    with open(temp_file_path, "w") as f:
        json.dump({"draft_a": draft_a, "draft_b": draft_b}, f)

    # Teclado con opciones A y B
    keyboard = {"inline_keyboard": [
        [
            {"text": "👍 Aprobar A", "callback_data": f"approve_A_{topic_id}"},
            {"text": "👍 Aprobar B", "callback_data": f"approve_B_{topic_id}"},
        ],
        [{"text": "👎 Rechazar Ambos", "callback_data": f"reject_{topic_id}"}]
    ]}
    
    message_text = (
        f"**Borradores Propuestos (ID: {topic_id}):**\n\n"
        f"--- **Opción A** ---\n{draft_a}\n\n"
        f"--- **Opción B** ---\n{draft_b}"
    )
    send_telegram_message(chat_id, message_text, reply_markup=keyboard)
    return True

# --- MANEJADOR DE CALLBACKS MODIFICADO ---
def handle_callback_query(update):
    query = update.get("callback_query", {})
    chat_id = query["message"]["chat"]["id"]
    message_id = query["message"]["message_id"]
    callback_data = query.get("data", "")
    
    action, _, topic_id = callback_data.partition('_')
    if '_' in action: # Maneja approve_A y approve_B
        action, option = action.split('_')
    
    original_message_text = query["message"].get("text", "")

    if action == "approve":
        temp_file_path = os.path.join(TEMP_DIR, f"{chat_id}_{topic_id}.tmp")
        edit_telegram_message(chat_id, message_id, original_message_text + f"\n\n✅ **¡Aprobada Opción {option}!**")
        try:
            with open(temp_file_path, "r") as f: tweets = json.load(f)
            
            chosen_tweet = tweets.get(f"draft_{option.lower()}", "")
            if not chosen_tweet: raise ValueError("Opción elegida no encontrada")
            
            # Guardar el tuit elegido en la memoria
            memory_collection = get_memory_collection()
            tweet_embedding = get_embedding(chosen_tweet)
            if tweet_embedding:
                memory_collection.add(embeddings=[tweet_embedding], documents=[chosen_tweet], ids=[topic_id])
                print(f"✅ Tuit (Opción {option}) guardado en 'memory_collection'.")

            intent_url = f"https://x.com/intent/tweet?text={quote_plus(chosen_tweet)}"
            keyboard = {"inline_keyboard": [[{"text": f"🚀 Publicar Opción {option}", "url": intent_url}]]}
            
            send_telegram_message(chat_id, "Usa el siguiente botón para publicar:", reply_markup=keyboard)
            send_telegram_message(chat_id, "Listo para el siguiente.", reply_markup=get_new_tweet_keyboard())
            
            if os.path.exists(temp_file_path): os.remove(temp_file_path)

        except Exception as e:
            print(f"Error en proceso de aprobación: {e}")

    elif action == "reject":
        edit_telegram_message(chat_id, message_id, original_message_text + "\n\n❌ **Rechazados.**", reply_markup=get_new_tweet_keyboard())
            
    elif action == "generate" or action == "generate_new":
        edit_telegram_message(chat_id, message_id, "🚀 Buscando un nuevo tema...")
        threading.Thread(target=do_the_work, args=(chat_id,)).start()

# --- (El resto del archivo no cambia) ---
@app.route(f"/{TELEGRAM_BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    # ... (contenido sin cambios) ...
    update = request.get_json()
    if "message" in update and update["message"].get("text") == "/generate":
        threading.Thread(target=do_the_work, args=(update["message"]["chat"]["id"],)).start()
    elif "callback_query" in update:
        handle_callback_query(update)
    return "ok", 200

@app.route("/")
def index():
    return "Bot is alive!", 200