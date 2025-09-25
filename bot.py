import os
import json
import time
import threading
from dotenv import load_dotenv
from flask import Flask, request
import requests
from urllib.parse import quote_plus

from core_generator import (
    find_relevant_topic, generate_tweet_from_topic, find_topic_by_id
)

load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

app = Flask(__name__)
TEMP_DIR = "/tmp"

def get_new_tweet_keyboard():
    """Crea el teclado con el botón para generar un nuevo tuit."""
    return {"inline_keyboard": [[{"text": "🚀 Generar Nuevo Tuit", "callback_data": "generate_new"}]]}

def send_telegram_message(chat_id, text, reply_markup=None):
    """Función centralizada para enviar mensajes a Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        requests.post(url, json=payload)
        print(f"Mensaje enviado a chat_id {chat_id}")
    except Exception as e:
        print(f"Error enviando mensaje a Telegram: {e}")

def edit_telegram_message(chat_id, message_id, text, reply_markup=None):
    """Función para editar un mensaje existente."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    requests.post(url, json=payload)

def do_the_work(chat_id):
    """Función que inicia el proceso de encontrar y proponer un tuit."""
    topic = find_relevant_topic()
    if topic:
        # --- CAMBIO CLAVE: Llama a generate_tweet_from_topic que devuelve 2 versiones en inglés ---
        eng_tweet_a, eng_tweet_b = generate_tweet_from_topic(topic.get("abstract"))
        propose_tweet(chat_id, topic, eng_tweet_a, eng_tweet_b)
    else:
        send_telegram_message(chat_id, "❌ No pude encontrar un tema relevante.", reply_markup=get_new_tweet_keyboard())

# --- MODIFICACIÓN: La función ahora recibe ambas versiones del tuit ---
def propose_tweet(chat_id, topic, eng_tweet_a, eng_tweet_b):
    """Genera un tuit y lo propone con botones de aprobación/rechazo."""
    topic_abstract = topic.get("abstract")
    topic_id = topic.get("topic_id")
    
    send_telegram_message(chat_id, f"✍️ Tema: '{topic_abstract}'.\nGenerando borrador...")
    
    if "Error:" in eng_tweet_a:
        send_telegram_message(chat_id, f"Hubo un problema: {eng_tweet_a}", reply_markup=get_new_tweet_keyboard())
        return

    temp_file_path = os.path.join(TEMP_DIR, f"{chat_id}_{topic_id}.tmp")
    # --- CAMBIO CLAVE: Guardar ambas versiones en el archivo temporal ---
    with open(temp_file_path, "w") as f:
        json.dump({"eng_a": eng_tweet_a, "eng_b": eng_tweet_b}, f)

    # --- CAMBIO CLAVE: Crear botones de aprobación para cada opción ---
    keyboard = {"inline_keyboard": [[
        {"text": "👍 Aprobar Opción A", "callback_data": f"approve_a_{topic_id}"},
        {"text": "👍 Aprobar Opción B", "callback_data": f"approve_b_{topic_id}"},
    ]]}
    
    message_text = (
        f"**Borrador Propuesto (ID: {topic_id}):**\n\n"
        f"**Opción A:**\n{eng_tweet_a}\n\n"
        f"**Opción B:**\n{eng_tweet_b}\n\n"
        f"¿Cuál de las dos quieres aprobar?"
    )
    send_telegram_message(chat_id, message_text, reply_markup=keyboard)

def handle_callback_query(update):
    """Maneja las pulsaciones de todos los botones."""
    query = update.get("callback_query", {})
    chat_id = query["message"]["chat"]["id"]
    message_id = query["message"]["message_id"]
    callback_data = query.get("data", "")
    
    action, version, topic_id = callback_data.split('_', 2)
    original_message_text = query["message"].get("text", "")

    if action == "approve":
        temp_file_path = os.path.join(TEMP_DIR, f"{chat_id}_{topic_id}.tmp")
        edit_telegram_message(chat_id, message_id, original_message_text + "\n\n✅ **¡Aprobado!**")
        try:
            with open(temp_file_path, "r") as f:
                tweets = json.load(f)
            
            # --- CAMBIO CLAVE: Seleccionar la versión aprobada ---
            if version == 'a':
                tweet_to_publish = tweets.get("eng_a", "")
            else:
                tweet_to_publish = tweets.get("eng_b", "")

            # Construir el enlace Web Intent
            eng_intent_url = f"https://x.com/intent/tweet?text={quote_plus(tweet_to_publish)}"

            # Crear el teclado con el enlace
            keyboard = {"inline_keyboard": [
                [{"text": "🚀 Publicar en X", "url": eng_intent_url}],
            ]}
            
            send_telegram_message(chat_id, "Usa el siguiente botón para publicar:", reply_markup=keyboard)
            send_telegram_message(chat_id, "Listo para el siguiente.", reply_markup=get_new_tweet_keyboard())
            
            if os.path.exists(temp_file_path): os.remove(temp_file_path)

        except Exception as e:
            print(f"Error en proceso de aprobación: {e}")
            send_telegram_message(chat_id, "❌ Error al procesar la aprobación.", reply_markup=get_new_tweet_keyboard())

    elif action == "reject":
        temp_file_path = os.path.join(TEMP_DIR, f"{chat_id}_{topic_id}.tmp")
        edit_telegram_message(chat_id, message_id, original_message_text + "\n\n❌ **Rechazado.**")
        topic = find_topic_by_id(topic_id)
        if topic:
            # --- CAMBIO CLAVE: Llama a generate_tweet_from_topic con el tema original ---
            eng_tweet_a, eng_tweet_b = generate_tweet_from_topic(topic.get("abstract"))
            threading.Thread(target=propose_tweet, args=(chat_id, topic, eng_tweet_a, eng_tweet_b)).start()
        else:
            send_telegram_message(chat_id, "❌ No pude encontrar el tema original.", reply_markup=get_new_tweet_keyboard())
        if os.path.exists(temp_file_path): os.remove(temp_file_path)
            
    elif callback_data == "generate_new":
        edit_telegram_message(chat_id, message_id, "🚀 Iniciando nuevo proceso...")
        threading.Thread(target=do_the_work, args=(chat_id,)).start()

@app.route(f"/{TELEGRAM_BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    update = request.get_json()
    if "message" in update and update["message"].get("text") == "/generate":
        threading.Thread(target=do_the_work, args=(update["message"]["chat"]["id"],)).start()
    elif "callback_query" in update:
        handle_callback_query(update)
    return "ok", 200

@app.route("/")
def index():
    return "Bot is alive!", 200