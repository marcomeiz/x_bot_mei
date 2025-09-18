import os
import json
import time
import threading
from dotenv import load_dotenv
from flask import Flask, request
import requests

from core_generator import (
    find_relevant_topic, generate_tweet_from_topic, find_topic_by_id, post_tweet_to_x
)

load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
X_USERNAME = "tu_usuario_de_x" # <--- ¡RECUERDA CAMBIAR ESTO!

app = Flask(__name__)

TEMP_DIR = "/tmp"

def get_new_tweet_keyboard():
    return {"inline_keyboard": [[{"text": "🚀 Generar Nuevo Tuit", "callback_data": "generate_new"}]]}

def send_telegram_message(chat_id, text, reply_markup=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup: payload["reply_markup"] = reply_markup
    try:
        requests.post(url, json=payload)
        print(f"Mensaje enviado a chat_id {chat_id}")
    except Exception as e:
        print(f"Error enviando mensaje a Telegram: {e}")

def edit_telegram_message(chat_id, message_id, text, reply_markup=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup: payload["reply_markup"] = reply_markup
    requests.post(url, json=payload)

def do_the_work(chat_id):
    topic = find_relevant_topic()
    if topic:
        propose_tweet(chat_id, topic)
    else:
        send_telegram_message(chat_id, "❌ No pude encontrar un tema relevante.", reply_markup=get_new_tweet_keyboard())

def propose_tweet(chat_id, topic):
    topic_abstract = topic.get("abstract")
    topic_id = topic.get("topic_id")
    
    send_telegram_message(chat_id, f"✍️ Tema: '{topic_abstract}'.\nGenerando borrador...")
    
    eng_tweet, spa_tweet = generate_tweet_from_topic(topic_abstract)
    
    if "Error:" in eng_tweet:
        send_telegram_message(chat_id, f"Hubo un problema: {eng_tweet}", reply_markup=get_new_tweet_keyboard())
        return

    temp_file_path = os.path.join(TEMP_DIR, f"{chat_id}_{topic_id}.tmp")
    # AÑADIMOS ESTADO DE PUBLICACIÓN
    with open(temp_file_path, "w") as f:
        json.dump({
            "eng": eng_tweet, "spa": spa_tweet,
            "eng_published": False, "spa_published": False
        }, f)

    keyboard = {"inline_keyboard": [[
        {"text": "👍 Publicar (ES)", "callback_data": f"approve_spa_{topic_id}"},
        {"text": "👍 Publicar (EN)", "callback_data": f"approve_eng_{topic_id}"},
        {"text": "👎 Rechazar", "callback_data": f"reject_{topic_id}"},
    ]]}
    
    message_text = f"**Borrador Propuesto (ID: {topic_id}):**\n\n**EN:**\n{eng_tweet}\n\n**ES:**\n{spa_tweet}\n\n¿Cuál quieres publicar?"
    send_telegram_message(chat_id, message_text, reply_markup=keyboard)

def handle_callback_query(update):
    query = update.get("callback_query", {})
    chat_id = query["message"]["chat"]["id"]
    message_id = query["message"]["message_id"]
    callback_data = query.get("data", "")
    
    parts = callback_data.split('_')
    action = parts[0]
    
    if action == "generate": # Maneja "generate_new"
        edit_telegram_message(chat_id, message_id, "🚀 Iniciando nuevo proceso...")
        threading.Thread(target=do_the_work, args=(chat_id,)).start()
        return

    # Para approve y reject, necesitamos el topic_id
    topic_id = parts[-1]
    temp_file_path = os.path.join(TEMP_DIR, f"{chat_id}_{topic_id}.tmp")
    
    try:
        with open(temp_file_path, "r") as f:
            state = json.load(f)
    except FileNotFoundError:
        edit_telegram_message(chat_id, message_id, query["message"]["text"] + "\n\n❌ Error: La sesión ha expirado (archivo temporal no encontrado).")
        send_telegram_message(chat_id, "Por favor, genera un nuevo tuit.", reply_markup=get_new_tweet_keyboard())
        return

    if action == "approve":
        lang = parts[1]
        tweet_to_post = state.get(lang)
        
        send_telegram_message(chat_id, f"✅ **Aprobado ({lang.upper()}).** Publicando en X...")
        
        response_data = post_tweet_to_x(tweet_to_post)
        
        if response_data and response_data.get("id"):
            tweet_id = response_data.get("id")
            tweet_url = f"https://x.com/{X_USERNAME}/status/{tweet_id}"
            send_telegram_message(chat_id, f"🚀 **¡Publicado!**\n{tweet_url}")
            
            # Actualizar estado
            state[f"{lang}_published"] = True
            with open(temp_file_path, "w") as f: json.dump(state, f)

        else:
            send_telegram_message(chat_id, "❌ Error al publicar en X. Revisa las credenciales/permisos.")

    elif action == "reject":
        edit_telegram_message(chat_id, message_id, query["message"]["text"] + "\n\n❌ **Rechazado.**")
        send_telegram_message(chat_id, "Ciclo cancelado.", reply_markup=get_new_tweet_keyboard())
        if os.path.exists(temp_file_path): os.remove(temp_file_path)
        return

    # --- LÓGICA DE ACTUALIZACIÓN DE BOTONES ---
    # Volvemos a leer el estado por si ha cambiado
    with open(temp_file_path, "r") as f:
        current_state = json.load(f)
    
    # Comprobamos si ya hemos terminado
    if current_state.get("eng_published") and current_state.get("spa_published"):
        edit_telegram_message(chat_id, message_id, query["message"]["text"] + "\n\n🎉 **Ambas versiones publicadas.**")
        send_telegram_message(chat_id, "¡Buen trabajo!", reply_markup=get_new_tweet_keyboard())
        if os.path.exists(temp_file_path): os.remove(temp_file_path)
    else:
        # Reconstruimos los botones que quedan
        remaining_buttons = []
        if not current_state.get("spa_published"):
            remaining_buttons.append({"text": "👍 Publicar (ES)", "callback_data": f"approve_spa_{topic_id}"})
        if not current_state.get("eng_published"):
            remaining_buttons.append({"text": "👍 Publicar (EN)", "callback_data": f"approve_eng_{topic_id}"})
        
        new_keyboard = {"inline_keyboard": [
            remaining_buttons,
            [{"text": "👎 Rechazar (finalizar)", "callback_data": f"reject_{topic_id}"}]
        ]}
        
        # Editamos el mensaje original con los botones actualizados
        edit_telegram_message(chat_id, message_id, query["message"]["text"], reply_markup=new_keyboard)

@app.route(f"/{TELEGRAM_BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    update = request.get_json()
    if "message" in update and update["message"].get("text") == "/generate":
        threading.Thread(target=do_the_work, args=(update["message"]["chat"]["id"],)).start()
    elif "callback_query" in update:
        # El manejo de callbacks ahora es más complejo, así que lo ponemos en un hilo
        threading.Thread(target=handle_callback_query, args=(update,)).start()
    return "ok", 200

@app.route("/")
def index():
    return "Bot is alive!", 200