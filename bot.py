import os
import json
import time
import threading
from dotenv import load_dotenv
from flask import Flask, request
import requests

# Importamos todas las funciones necesarias, incluyendo post_tweet_to_x
from core_generator import (
    find_relevant_topic, generate_tweet_from_topic, find_topic_by_id, post_tweet_to_x
)

load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
X_USERNAME = "marcomeiz" # <--- ¡RECUERDA CAMBIAR ESTO!

app = Flask(__name__)

def get_new_tweet_keyboard():
    """Crea y devuelve el teclado con el botón para generar un nuevo tuit."""
    keyboard = {"inline_keyboard": [[{"text": "🚀 Generar Nuevo Tuit", "callback_data": "generate_new"}]]}
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
    """Función para editar un mensaje existente."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    requests.post(url, json=payload)

def do_the_work(chat_id):
    """Función principal que se ejecuta en segundo plano."""
    print(f"Iniciando búsqueda de tema para el chat_id: {chat_id}")
    topic = find_relevant_topic()
    if topic:
        propose_tweet(chat_id, topic)
    else:
        send_telegram_message(chat_id, "❌ No pude encontrar un tema relevante en la base de datos.", reply_markup=get_new_tweet_keyboard())

def propose_tweet(chat_id, topic):
    """Genera un tuit para un tema y lo propone con botones."""
    topic_abstract = topic.get("abstract")
    topic_id = topic.get("topic_id")
    
    send_telegram_message(chat_id, f"✍️ Tema: '{topic_abstract}'.\nGenerando borrador...")
    
    eng_tweet, spa_tweet = generate_tweet_from_topic(topic_abstract)
    
    if "Error:" in eng_tweet:
        send_telegram_message(chat_id, f"Hubo un problema: {eng_tweet}", reply_markup=get_new_tweet_keyboard())
        return

    # Guardamos temporalmente los tuits para la aprobación
    with open(f"{chat_id}_{topic_id}.tmp", "w") as f:
        json.dump({"eng": eng_tweet, "spa": spa_tweet}, f)

    keyboard = {"inline_keyboard": [[
        {"text": "👍 Publicar (ES)", "callback_data": f"approve_spa_{topic_id}"},
        {"text": "👍 Publicar (EN)", "callback_data": f"approve_eng_{topic_id}"},
        {"text": "👎 Rechazar", "callback_data": f"reject_{topic_id}"},
    ]]}
    
    message_text = f"**Borrador Propuesto (ID: {topic_id}):**\n\n**EN:**\n{eng_tweet}\n\n**ES:**\n{spa_tweet}\n\n¿Cuál quieres publicar?"
    send_telegram_message(chat_id, message_text, reply_markup=keyboard)

def handle_callback_query(update):
    """Maneja las pulsaciones de todos los botones."""
    query = update.get("callback_query", {})
    chat_id = query["message"]["chat"]["id"]
    message_id = query["message"]["message_id"]
    callback_data = query.get("data", "")
    
    parts = callback_data.split('_')
    action = parts[0]
    
    original_message_text = query["message"].get("text", "")

    if action == "approve":
        lang = parts[1]
        topic_id = parts[2]
        edit_telegram_message(chat_id, message_id, original_message_text + f"\n\n✅ **Aprobado ({lang.upper()}).** Publicando en X...")
        try:
            with open(f"{chat_id}_{topic_id}.tmp", "r") as f: tweets = json.load(f)
            tweet_to_post = tweets.get(lang)
            if tweet_to_post:
                response_data = post_tweet_to_x(tweet_to_post)
                if response_data and response_data.get("id"):
                    tweet_id = response_data.get("id")
                    tweet_url = f"https://x.com/{X_USERNAME}/status/{tweet_id}"
                    send_telegram_message(chat_id, f"🚀 **¡Publicado con éxito!**\n\nPuedes verlo aquí: {tweet_url}", reply_markup=get_new_tweet_keyboard())
                else:
                    send_telegram_message(chat_id, "❌ Error al publicar en X.", reply_markup=get_new_tweet_keyboard())
            if os.path.exists(f"{chat_id}_{topic_id}.tmp"): os.remove(f"{chat_id}_{topic_id}.tmp")
        except Exception as e:
            print(f"Error en proceso de aprobación: {e}")
            send_telegram_message(chat_id, "❌ Error al procesar la aprobación.", reply_markup=get_new_tweet_keyboard())

    elif action == "reject":
        topic_id = parts[1]
        edit_telegram_message(chat_id, message_id, original_message_text + "\n\n❌ **Rechazado.**")
        topic = find_topic_by_id(topic_id)
        if topic:
            threading.Thread(target=propose_tweet, args=(chat_id, topic)).start()
        else:
            send_telegram_message(chat_id, "❌ No pude encontrar el tema original.", reply_markup=get_new_tweet_keyboard())
        if os.path.exists(f"{chat_id}_{topic_id}.tmp"): os.remove(f"{chat_id}_{topic_id}.tmp")
            
    elif action == "generate":
        edit_telegram_message(chat_id, message_id, "🚀 Iniciando nuevo proceso...")
        threading.Thread(target=do_the_work, args=(chat_id,)).start()

@app.route(f"/{TELEGRAM_BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    if request.is_json:
        update = request.get_json()
        if "message" in update and "text" in update["message"] and update["message"]["text"] == "/generate":
            chat_id = update["message"]["chat"]["id"]
            send_telegram_message(chat_id, "🤖 Comando recibido. Iniciando proceso...")
            threading.Thread(target=do_the_work, args=(chat_id,)).start()
        elif "callback_query" in update:
            handle_callback_query(update)
    return "ok", 200

@app.route("/")
def index():
    return "Bot is alive!", 200