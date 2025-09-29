# bot.py
import os
import json
import threading
from dotenv import load_dotenv
from flask import Flask, request
import requests
from urllib.parse import quote_plus

# --- NUEVO: Importar el logger configurado ---
from logger_config import logger

from embeddings_manager import get_embedding, get_memory_collection
from core_generator import find_relevant_topic, generate_tweet_from_topic, find_topic_by_id

load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
app = Flask(__name__)
TEMP_DIR = "/tmp"

# --- (Funciones de Telegram sin cambios) ---
def get_new_tweet_keyboard():
    return {"inline_keyboard": [[{"text": "🚀 Generar Nuevo Tuit", "callback_data": "generate_new"}]]}

def _post_telegram(url, payload, chat_id):
    try:
        r = requests.post(url, json=payload, timeout=20)
        data = {}
        try:
            data = r.json()
        except Exception:
            pass
        if r.status_code != 200 or not data.get("ok", True):
            logger.error(f"[CHAT_ID: {chat_id}] Telegram API error: status={r.status_code}, resp={data}")
            return False
        return True
    except Exception as e:
        logger.error(f"[CHAT_ID: {chat_id}] Telegram HTTP error: {e}", exc_info=True)
        return False

def send_telegram_message(chat_id, text, reply_markup=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    # Intento 1: con Markdown simple (puede fallar por entidades)
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup: payload["reply_markup"] = reply_markup
    if _post_telegram(url, payload, chat_id):
        return True
    # Intento 2: sin parse_mode (texto plano)
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup: payload["reply_markup"] = reply_markup
    return _post_telegram(url, payload, chat_id)

def edit_telegram_message(chat_id, message_id, text, reply_markup=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup: payload["reply_markup"] = reply_markup
    if _post_telegram(url, payload, chat_id):
        return True
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if reply_markup: payload["reply_markup"] = reply_markup
    return _post_telegram(url, payload, chat_id)


# --- (Funciones principales con logs añadidos) ---
def do_the_work(chat_id):
    logger.info(f"[CHAT_ID: {chat_id}] Iniciando nuevo ciclo de trabajo 'do_the_work'.")
    max_retries = 5
    for i in range(max_retries):
        logger.info(f"[CHAT_ID: {chat_id}] Intento {i+1}/{max_retries} de encontrar un tema relevante.")
        topic = find_relevant_topic()
        if topic:
            if propose_tweet(chat_id, topic):
                logger.info(f"[CHAT_ID: {chat_id}] Propuesta enviada con éxito. Finalizando ciclo.")
                return
            logger.warning(f"[CHAT_ID: {chat_id}] Tema '{topic.get('topic_id')}' descartado por similitud. Buscando otro.")
            send_telegram_message(chat_id, "⚠️ Tema descartado por ser muy similar a uno anterior. Buscando otro...")
        else:
            logger.error(f"[CHAT_ID: {chat_id}] No se pudo encontrar un tema relevante en la base de datos.")
            send_telegram_message(chat_id, "❌ No pude encontrar un tema relevante.", reply_markup=get_new_tweet_keyboard())
            return
    logger.error(f"[CHAT_ID: {chat_id}] No se pudo encontrar un tema único tras {max_retries} intentos.")
    send_telegram_message(chat_id, f"❌ No pude encontrar un tema único tras {max_retries} intentos.", reply_markup=get_new_tweet_keyboard())

def propose_tweet(chat_id, topic):
    topic_abstract = topic.get("abstract")
    topic_id = topic.get("topic_id")
    source_pdf = topic.get("source_pdf")
    logger.info(f"[CHAT_ID: {chat_id}] Tema seleccionado (ID: {topic_id}). Abstract: '{topic_abstract[:80]}...'")
    if source_pdf:
        send_telegram_message(chat_id, f"✍️ Tema: '{topic_abstract[:80]}...'.\n📄 Origen: {source_pdf}\nGenerando 2 alternativas...")
    else:
        send_telegram_message(chat_id, f"✍️ Tema: '{topic_abstract[:80]}...'.\nGenerando 2 alternativas...")

    draft_a, draft_b = generate_tweet_from_topic(topic_abstract)

    if "Error: El tema es demasiado similar" in draft_a:
        return False  # Reintentar con otro tema
    if "Error:" in draft_a:
        logger.error(f"[CHAT_ID: {chat_id}] Error recibido de 'generate_tweet_from_topic': {draft_a}")
        send_telegram_message(chat_id, f"Hubo un problema: {draft_a}", reply_markup=get_new_tweet_keyboard())
        return False  # Indicar al bucle que intente con otro tema

    temp_file_path = os.path.join(TEMP_DIR, f"{chat_id}_{topic_id}.tmp")
    logger.info(f"[CHAT_ID: {chat_id}] Guardando borradores en archivo temporal: {temp_file_path}")
    with open(temp_file_path, "w") as f:
        json.dump({"draft_a": draft_a, "draft_b": draft_b}, f)

    # Contadores de caracteres para visibilidad
    len_a = len(draft_a)
    len_b = len(draft_b)

    keyboard = {"inline_keyboard": [
        [
            {"text": "👍 Aprobar A", "callback_data": f"approve_A_{topic_id}"},
            {"text": "👍 Aprobar B", "callback_data": f"approve_B_{topic_id}"},
        ],
        [{"text": "👎 Rechazar Ambos", "callback_data": f"reject_{topic_id}"}]
    ]}

    header_lines = [f"**Borradores Propuestos (ID: {topic_id})**"]
    if topic_abstract:
        header_lines.append(f"Tema: {topic_abstract}")
    if source_pdf:
        header_lines.append(f"Origen: {source_pdf}")
    header = "\n".join(header_lines)

    message_text = (
        f"{header}\n\n"
        f"--- **Opción A** ({len_a}/280) ---\n{draft_a}\n\n"
        f"--- **Opción B** ({len_b}/280) ---\n{draft_b}"
    )
    logger.info(f"[CHAT_ID: {chat_id}] Enviando propuestas (A/B) al usuario para el topic ID: {topic_id}.")
    if send_telegram_message(chat_id, message_text, reply_markup=keyboard):
        return True
    logger.error(f"[CHAT_ID: {chat_id}] Falló el envío de propuestas por Telegram (ID: {topic_id}).")
    return False

def handle_callback_query(update):
    query = update.get("callback_query", {})
    chat_id = query["message"]["chat"]["id"]
    message_id = query["message"]["message_id"]
    callback_data = query.get("data", "")
    logger.info(f"[CHAT_ID: {chat_id}] Callback recibido: '{callback_data}'")

    # Estructuras esperadas:
    #  - approve_A_<topic_id>
    #  - approve_B_<topic_id>
    #  - reject_<topic_id>
    parts = callback_data.split('_', 2)
    action = parts[0] if parts else ""
    option = parts[1] if len(parts) >= 2 and action == "approve" else ""
    topic_id = parts[2] if len(parts) == 3 else (parts[1] if len(parts) == 2 else "")

    original_message_text = query["message"].get("text", "")

    if action == "approve":
        temp_file_path = os.path.join(TEMP_DIR, f"{chat_id}_{topic_id}.tmp")
        logger.info(f"[CHAT_ID: {chat_id}] Aprobada Opción {option} para topic ID: {topic_id}.")
        edit_telegram_message(chat_id, message_id, original_message_text + f"\n\n✅ **¡Aprobada Opción {option}!**")
        try:
            if not os.path.exists(temp_file_path):
                raise FileNotFoundError(f"Temp file missing: {temp_file_path}")
            with open(temp_file_path, "r") as f:
                tweets = json.load(f)

            chosen_tweet = tweets.get(f"draft_{option.lower()}", "")
            if not chosen_tweet: raise ValueError("Opción elegida no encontrada")

            memory_collection = get_memory_collection()
            tweet_embedding = get_embedding(chosen_tweet)
            if tweet_embedding:
                logger.info(f"[CHAT_ID: {chat_id}] Guardando tuit aprobado (ID: {topic_id}) en 'memory_collection'.")
                memory_collection.add(embeddings=[tweet_embedding], documents=[chosen_tweet], ids=[topic_id])
                logger.info(f"[CHAT_ID: {chat_id}] Tuit guardado con éxito en memoria.")

            intent_url = f"https://x.com/intent/tweet?text={quote_plus(chosen_tweet)}"
            keyboard = {"inline_keyboard": [[{"text": f"🚀 Publicar Opción {option}", "url": intent_url}]]}

            send_telegram_message(chat_id, "Usa el siguiente botón para publicar:", reply_markup=keyboard)
            send_telegram_message(chat_id, "Listo para el siguiente.", reply_markup=get_new_tweet_keyboard())

            if os.path.exists(temp_file_path):
                logger.info(f"[CHAT_ID: {chat_id}] Eliminando archivo temporal: {temp_file_path}")
                os.remove(temp_file_path)

        except Exception as e:
            logger.error(f"[CHAT_ID: {chat_id}] Error crítico en el proceso de aprobación: {e}", exc_info=True)
            send_telegram_message(chat_id, "⚠️ No pude recuperar el borrador aprobado (quizá expiró). Genera uno nuevo con el botón.", reply_markup=get_new_tweet_keyboard())

    elif action == "reject":
        logger.info(f"[CHAT_ID: {chat_id}] Ambas opciones rechazadas para topic ID: {topic_id}.")
        edit_telegram_message(chat_id, message_id, original_message_text + "\n\n❌ **Rechazados.**", reply_markup=get_new_tweet_keyboard())

    elif "generate" in callback_data: # Maneja "generate" y "generate_new"
        logger.info(f"[CHAT_ID: {chat_id}] El usuario ha solicitado un nuevo tuit manualmente.")
        edit_telegram_message(chat_id, message_id, "🚀 Buscando un nuevo tema...")
        threading.Thread(target=do_the_work, args=(chat_id,)).start()

@app.route(f"/{TELEGRAM_BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    update = request.get_json()
    if "message" in update and update["message"].get("text") == "/generate":
        chat_id = update["message"]["chat"]["id"]
        logger.info(f"[CHAT_ID: {chat_id}] Comando '/generate' recibido.")
        threading.Thread(target=do_the_work, args=(chat_id,)).start()
    elif "callback_query" in update:
        handle_callback_query(update)
    return "ok", 200

@app.route("/")
def index():
    return "Bot is alive!", 200
