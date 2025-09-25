# bot.py
import os
import json
import threading
from dotenv import load_dotenv
from flask import Flask, request
import requests
from urllib.parse import quote_plus

# MODIFICADO: Importamos las funciones 'get'
from embeddings_manager import get_embedding, get_memory_collection
from core_generator import find_relevant_topic, generate_tweet_from_topic, find_topic_by_id

load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
app = Flask(__name__)
TEMP_DIR = "/tmp"

def get_new_tweet_keyboard():
    # CORREGIDO: El callback_data ahora es 'generate_new' para que sea único
    return {"inline_keyboard": [[{"text": "🚀 Generar Nuevo Tuit", "callback_data": "generate_new"}]]}

# ... (send_telegram_message y edit_telegram_message no cambian) ...
def send_telegram_message(chat_id, text, reply_markup=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup: payload["reply_markup"] = reply_markup
    try:
        requests.post(url, json=payload)
        print(f"Mensaje enviado a chat_id {chat_id}")
    except Exception as e: print(f"Error enviando mensaje: {e}")

def edit_telegram_message(chat_id, message_id, text, reply_markup=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup: payload["reply_markup"] = reply_markup
    requests.post(url, json=payload)


def do_the_work(chat_id):
    max_retries = 5
    for attempt in range(max_retries):
        topic = find_relevant_topic()
        if topic:
            success = propose_tweet(chat_id, topic)
            if success: return
            print(f"Intento {attempt + 1}: Tema repetitivo, buscando otro...")
            send_telegram_message(chat_id, "⚠️ Tema descartado por ser muy similar a uno anterior. Buscando otro...")
        else:
            send_telegram_message(chat_id, "❌ No pude encontrar un tema relevante.", reply_markup=get_new_tweet_keyboard())
            return
    send_telegram_message(chat_id, "❌ No pude encontrar un tema único tras varios intentos.", reply_markup=get_new_tweet_keyboard())

def propose_tweet(chat_id, topic):
    topic_abstract = topic.get("abstract")
    topic_id = topic.get("topic_id")
    send_telegram_message(chat_id, f"✍️ Tema: '{topic_abstract[:80]}...'.\nGenerando borrador...")
    eng_tweet, spa_tweet = generate_tweet_from_topic(topic_abstract)
    if "Error: El tema es demasiado similar" in eng_tweet:
        return False
    if "Error:" in eng_tweet:
        send_telegram_message(chat_id, f"Hubo un problema: {eng_tweet}", reply_markup=get_new_tweet_keyboard())
        return True
    temp_file_path = os.path.join(TEMP_DIR, f"{chat_id}_{topic_id}.tmp")
    with open(temp_file_path, "w") as f: json.dump({"eng": eng_tweet, "spa": spa_tweet}, f)
    keyboard = {"inline_keyboard": [[
        {"text": "👍 Aprobar", "callback_data": f"approve_{topic_id}"},
        {"text": "👎 Rechazar", "callback_data": f"reject_{topic_id}"},
    ]]}
    message_text = f"**Borrador Propuesto (ID: {topic_id}):**\n\n**EN:**\n{eng_tweet}\n\n**ES:**\n{spa_tweet}"
    send_telegram_message(chat_id, message_text, reply_markup=keyboard)
    return True

def handle_callback_query(update):
    query = update.get("callback_query", {})
    chat_id = query["message"]["chat"]["id"]
    message_id = query["message"]["message_id"]
    callback_data = query.get("data", "")
    action, _, topic_id = callback_data.partition('_')
    original_message_text = query["message"].get("text", "")

    if action == "approve":
        # MODIFICADO: Obtenemos la colección de memoria aquí
        memory_collection = get_memory_collection()
        temp_file_path = os.path.join(TEMP_DIR, f"{chat_id}_{topic_id}.tmp")
        edit_telegram_message(chat_id, message_id, original_message_text + "\n\n✅ **¡Aprobado!**")
        try:
            with open(temp_file_path, "r") as f: tweets = json.load(f)
            eng_tweet = tweets.get("eng", "")
            spa_tweet = tweets.get("spa", "")
            print("🧠 Guardando tuit en la memoria a largo plazo...")
            tweet_embedding = get_embedding(eng_tweet)
            if tweet_embedding:
                memory_collection.add(embeddings=[tweet_embedding], documents=[eng_tweet], ids=[topic_id])
                print("✅ Tuit guardado en 'memory_collection'.")
            eng_intent_url = f"https://x.com/intent/tweet?text={quote_plus(eng_tweet)}"
            spa_intent_url = f"https://x.com/intent/tweet?text={quote_plus(spa_tweet)}"
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
        # ... (contenido de la función sin cambios) ...
        temp_file_path = os.path.join(TEMP_DIR, f"{chat_id}_{topic_id}.tmp")
        edit_telegram_message(chat_id, message_id, original_message_text + "\n\n❌ **Rechazado.**")
        topic = find_topic_by_id(topic_id)
        if topic: threading.Thread(target=propose_tweet, args=(chat_id, topic)).start()
        else: send_telegram_message(chat_id, "❌ No pude encontrar el tema original.", reply_markup=get_new_tweet_keyboard())
        if os.path.exists(temp_file_path): os.remove(temp_file_path)
            
    # CORREGIDO: Añadimos el manejo del botón 'generate_new'
    elif action == "generate" or action == "generate_new":
        edit_telegram_message(chat_id, message_id, "🚀 Buscando un nuevo tema...")
        threading.Thread(target=do_the_work, args=(chat_id,)).start()

@app.route(f"/{TELEGRAM_BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    update = request.get_json()
    print(f"--- UPDATE RECIBIDO ---\n{json.dumps(update, indent=2)}\n-----------------------")
    if "message" in update and update["message"].get("text") == "/generate":
        print("Comando /generate detectado. Iniciando hilo...")
        threading.Thread(target=do_the_work, args=(update["message"]["chat"]["id"],)).start()
    elif "callback_query" in update:
        print("Callback query detectado. Manejando...")
        handle_callback_query(update)
    else:
        print("Update no procesable. Ignorando.")
    return "ok", 200

@app.route("/")
def index():
    return "Bot is alive!", 200