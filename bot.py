import os
import json
import time
import threading
from dotenv import load_dotenv
from flask import Flask, request
import requests

from core_generator import find_relevant_topic, generate_tweet_from_topic, find_topic_by_id

load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

app = Flask(__name__)

def get_new_tweet_keyboard():
    """Crea y devuelve el teclado con el botón para generar un nuevo tuit."""
    keyboard = {
        "inline_keyboard": [[
            {"text": "🚀 Generar Nuevo Tuit", "callback_data": "generate_new"},
        ]]
    }
    return keyboard

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
    """Función para editar un mensaje existente (ej. para quitar botones)."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    requests.post(url, json=payload)

def propose_tweet(chat_id, topic):
    """Genera un tuit para un tema y lo propone con botones de aprobación/rechazo."""
    topic_abstract = topic.get("abstract")
    topic_id = topic.get("topic_id")

    send_telegram_message(chat_id, f"✍️ Tema: '{topic_abstract}'.\nGenerando borrador...")
    
    eng_tweet, spa_tweet = generate_tweet_from_topic(topic_abstract)
    
    if "Error:" in eng_tweet:
        send_telegram_message(chat_id, f"Hubo un problema: {eng_tweet}", reply_markup=get_new_tweet_keyboard())
        return

    keyboard = {
        "inline_keyboard": [[
            {"text": "👍 Aprobar", "callback_data": f"approve_{topic_id}"},
            {"text": "👎 Rechazar", "callback_data": f"reject_{topic_id}"},
        ]]
    }
    
    message_text = f"**Borrador Propuesto (ID: {topic_id}):**\n\n**EN:**\n{eng_tweet}\n\n**ES:**\n{spa_tweet}\n\n¿Aprobar?"
    send_telegram_message(chat_id, message_text, reply_markup=keyboard)

def handle_generate_command(chat_id):
    """Maneja la lógica de encontrar un tema y proponer un tuit."""
    send_telegram_message(chat_id, "🤖 Buscando un nuevo tema relevante...")
    topic = find_relevant_topic()
    if topic:
        propose_tweet(chat_id, topic)
    else:
        send_telegram_message(chat_id, "❌ No pude encontrar un tema relevante en la base de datos.", reply_markup=get_new_tweet_keyboard())

def handle_callback_query(update):
    """Maneja las pulsaciones de todos los botones."""
    query = update.get("callback_query", {})
    chat_id = query["message"]["chat"]["id"]
    message_id = query["message"]["message_id"]
    callback_data = query.get("data", "")
    
    action, _, topic_id = callback_data.partition('_')
    
    original_message_text = query["message"].get("text", "")

    if action == "approve":
        edit_telegram_message(chat_id, message_id, original_message_text + "\n\n✅ **¡Aprobado!**")
        send_telegram_message(chat_id, "Listo para el siguiente.", reply_markup=get_new_tweet_keyboard())
        
    elif action == "reject":
        edit_telegram_message(chat_id, message_id, original_message_text + "\n\n❌ **Rechazado.**")
        send_telegram_message(chat_id, "Generando una nueva versión sobre el mismo tema...")
        topic = find_topic_by_id(topic_id)
        if topic:
            threading.Thread(target=propose_tweet, args=(chat_id, topic)).start()
        else:
            send_telegram_message(chat_id, "❌ No pude encontrar el tema original para regenerar.", reply_markup=get_new_tweet_keyboard())
            
    elif action == "generate" and topic_id == "new":
        # Edita el mensaje anterior para quitar el botón y que no se pueda pulsar dos veces
        edit_telegram_message(chat_id, message_id, "Iniciando nuevo proceso...")
        threading.Thread(target=handle_generate_command, args=(chat_id,)).start()

@app.route(f"/{TELEGRAM_BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    if request.is_json:
        update = request.get_json()
        if "message" in update and "text" in update["message"] and update["message"]["text"] == "/generate":
            chat_id = update["message"]["chat"]["id"]
            threading.Thread(target=handle_generate_command, args=(chat_id,)).start()
        elif "callback_query" in update:
            handle_callback_query(update)
    return "ok", 200

@app.route("/")
def index():
    return "Bot is alive!", 200