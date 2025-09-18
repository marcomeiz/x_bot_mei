import os
from dotenv import load_dotenv
from flask import Flask, request
import threading
import time

# MODIFICADO: Importamos las funciones con sus nuevos parámetros
from core_generator import find_relevant_topic, generate_tweet_from_topic, find_topic_by_id, send_progress_update

load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

app = Flask(__name__)

def get_new_tweet_keyboard():
    keyboard = {"inline_keyboard": [[{"text": "🚀 Generar Nuevo Tuit", "callback_data": "generate_new"}]]}
    return keyboard

# La función send_telegram_message ahora reside en core_generator.py para ser usada por ambos
# La renombramos aquí para claridad
send_final_message = send_progress_update

def edit_telegram_message(chat_id, message_id, text, reply_markup=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup: payload["reply_markup"] = reply_markup
    requests.post(url, json=payload)

def do_the_work(chat_id):
    print(f"Iniciando búsqueda de tema para el chat_id: {chat_id}")
    topic = find_relevant_topic(chat_id) # MODIFICADO: Pasamos el chat_id
    if topic:
        propose_tweet(chat_id, topic)
    else:
        send_final_message(chat_id, "❌ No pude encontrar un tema relevante en la base de datos.", reply_markup=get_new_tweet_keyboard())

def propose_tweet(chat_id, topic):
    topic_abstract = topic.get("abstract")
    topic_id = topic.get("topic_id")
    
    eng_tweet, spa_tweet = generate_tweet_from_topic(topic_abstract, chat_id) # MODIFICADO: Pasamos el chat_id
    
    if "Error:" in eng_tweet:
        send_final_message(chat_id, f"Hubo un problema: {eng_tweet}", reply_markup=get_new_tweet_keyboard())
        return

    keyboard = {"inline_keyboard": [[
        {"text": "👍 Aprobar", "callback_data": f"approve_{topic_id}"},
        {"text": "👎 Rechazar", "callback_data": f"reject_{topic_id}"},
    ]]}
    
    message_text = f"**Borrador Propuesto (ID: {topic_id}):**\n\n**EN:**\n{eng_tweet}\n\n**ES:**\n{spa_tweet}\n\n¿Aprobar?"
    send_final_message(chat_id, message_text, reply_markup=keyboard)

def handle_callback_query(update):
    query = update.get("callback_query", {})
    chat_id = query["message"]["chat"]["id"]
    message_id = query["message"]["message_id"]
    callback_data = query.get("data", "")
    
    action, _, topic_id = callback_data.partition('_')
    original_message_text = query["message"].get("text", "")

    if action == "approve":
        edit_telegram_message(chat_id, message_id, original_message_text + "\n\n✅ **¡Aprobado!**")
        send_final_message(chat_id, "Listo para el siguiente.", reply_markup=get_new_tweet_keyboard())
        
    elif action == "reject":
        edit_telegram_message(chat_id, message_id, original_message_text + "\n\n❌ **Rechazado.**")
        topic = find_topic_by_id(topic_id)
        if topic:
            threading.Thread(target=propose_tweet, args=(chat_id, topic)).start()
        else:
            send_final_message(chat_id, "❌ No pude encontrar el tema original.", reply_markup=get_new_tweet_keyboard())
            
    elif action == "generate" and topic_id == "new":
        edit_telegram_message(chat_id, message_id, "🚀 Iniciando nuevo proceso...")
        threading.Thread(target=do_the_work, args=(chat_id,)).start()

@app.route(f"/{TELEGRAM_BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    if request.is_json:
        update = request.get_json()
        if "message" in update and "text" in update["message"] and update["message"]["text"] == "/generate":
            chat_id = update["message"]["chat"]["id"]
            send_final_message(chat_id, "🤖 Comando recibido. Despertando a la IA...")
            threading.Thread(target=do_the_work, args=(chat_id,)).start()
        elif "callback_query" in update:
            handle_callback_query(update)
    return "ok", 200

@app.route("/")
def index():
    return "Bot is alive!", 200