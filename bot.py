import os
import json
import time
import threading
from dotenv import load_dotenv
from flask import Flask, request
import requests
from urllib.parse import quote_plus # Nueva librería para formatear el texto para URLs

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
        propose_tweet(chat_id, topic)
    else:
        send_telegram_message(chat_id, "❌ No pude encontrar un tema relevante.", reply_markup=get_new_tweet_keyboard())

def propose_tweet(chat_id, topic):
    """Genera un tuit y lo propone con botones de aprobación/rechazo."""
    topic_abstract = topic.get("abstract")
    topic_id = topic.get("topic_id")
    
    send_telegram_message(chat_id, f"✍️ Tema: '{topic_abstract}'.\nGenerando borrador...")
    
    eng_tweet, spa_tweet = generate_tweet_from_topic(topic_abstract)
    
    if "Error:" in eng_tweet:
        send_telegram_message(chat_id, f"Hubo un problema: {eng_tweet}", reply_markup=get_new_tweet_keyboard())
        return

    temp_file_path = os.path.join(TEMP_DIR, f"{chat_id}_{topic_id}.tmp")
    with open(temp_file_path, "w") as f:
        json.dump({"eng": eng_tweet, "spa": spa_tweet}, f)

    keyboard = {"inline_keyboard": [[
        {"text": "👍 Aprobar", "callback_data": f"approve_{topic_id}"},
        {"text": "👎 Rechazar", "callback_data": f"reject_{topic_id}"},
    ]]}
    
    message_text = f"**Borrador Propuesto (ID: {topic_id}):**\n\n**EN:**\n{eng_tweet}\n\n**ES:**\n{spa_tweet}\n\n¿Aprobar?"
    send_telegram_message(chat_id, message_text, reply_markup=keyboard)

def handle_callback_query(update):
    """Maneja las pulsaciones de todos los botones."""
    query = update.get("callback_query", {})
    chat_id = query["message"]["chat"]["id"]
    message_id = query["message"]["message_id"]
    callback_data = query.get("data", "")
    
    action, _, topic_id = callback_data.partition('_')
    original_message_text = query["message"].get("text", "")

    if action == "approve":
        temp_file_path = os.path.join(TEMP_DIR, f"{chat_id}_{topic_id}.tmp")
        edit_telegram_message(chat_id, message_id, original_message_text + "\n\n✅ **¡Aprobado!**")
        try:
            with open(temp_file_path, "r") as f:
                tweets = json.load(f)
            
            eng_tweet = tweets.get("eng", "")
            spa_tweet = tweets.get("spa", "")

            # Construir los enlaces Web Intent
            eng_intent_url = f"https://x.com/intent/tweet?text={quote_plus(eng_tweet)}"
            spa_intent_url = f"https://x.com/intent/tweet?text={quote_plus(spa_tweet)}"

            # Crear el teclado con los enlaces
            keyboard = {"inline_keyboard": [
                [{"text": "🚀 Abrir en X (EN)", "url": eng_intent_url}],
                [{"text": "🚀 Abrir en X (ES)", "url": spa_intent_url}]
            ]}
            
            send_telegram_message(chat_id, "Usa los siguientes botones para publicar:", reply_markup=keyboard)
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
            threading.Thread(target=propose_tweet, args=(chat_id, topic)).start()
        else:
            send_telegram_message(chat_id, "❌ No pude encontrar el tema original.", reply_markup=get_new_tweet_keyboard())
        if os.path.exists(temp_file_path): os.remove(temp_file_path)
            
    elif action == "generate":
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