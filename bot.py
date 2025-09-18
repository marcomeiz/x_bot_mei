import os
import json
import time
import threading
import subprocess
from datetime import datetime
from dotenv import load_dotenv
from flask import Flask, request
import requests

# Importamos la función que hace el trabajo pesado
from core_generator import generate_single_tweet

# Cargar configuración
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Inicializar la aplicación web Flask
app = Flask(__name__)

def get_status_message():
    """Prepara el mensaje de estado, incluyendo la versión del código."""
    try:
        # Intenta obtener el hash del último commit de Git
        commit_hash = subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD']).decode('utf-8').strip()
        version_info = f"Versión del código: `{commit_hash}`"
    except Exception:
        version_info = "Versión del código: No disponible"

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
    
    status_message = (
        f"✅ **Ghostwriter Bot está en línea.**\n\n"
        f"{version_info}\n"
        f"Último reinicio: {timestamp}"
    )
    return status_message

def send_telegram_message(chat_id, text):
    """Función auxiliar para enviar mensajes de vuelta a Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = { "chat_id": chat_id, "text": text, "parse_mode": "Markdown" }
    try:
        requests.post(url, json=payload)
        print(f"Mensaje enviado a chat_id {chat_id}")
    except Exception as e:
        print(f"Error sending message to Telegram: {e}")

def do_the_work(chat_id):
    """Función que se ejecuta en segundo plano para generar el tuit."""
    print(f"Iniciando generación de tuit para el chat_id: {chat_id}")
    send_telegram_message(chat_id, "🤖 Entendido. Buscando un tema y empezando el proceso creativo... Dame un minuto.")

    eng_tweet, spa_tweet = generate_single_tweet()

    if "Error:" in eng_tweet:
        send_telegram_message(chat_id, f"Hubo un problema: {eng_tweet}")
    else:
        if eng_tweet:
            send_telegram_message(chat_id, f"**English Version Approved:**\n\n{eng_tweet}")
        if spa_tweet:
            time.sleep(1)
            send_telegram_message(chat_id, f"**Versión en Español Aprobada:**\n\n{spa_tweet}")
    print(f"Proceso completado para el chat_id: {chat_id}")

@app.route(f"/{TELEGRAM_BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    """Esta función se activa cuando Telegram nos envía un mensaje."""
    if request.is_json:
        update = request.get_json()
        
        if "message" in update and "text" in update["message"]:
            chat_id = update["message"]["chat"]["id"]
            text = update["message"]["text"]

            if text == "/generate":
                thread = threading.Thread(target=do_the_work, args=(chat_id,))
                thread.start()
            
            # --- NUEVO: Manejador para el comando /status ---
            elif text == "/status":
                print(f"Recibido comando /status del chat_id: {chat_id}")
                status_text = get_status_message()
                send_telegram_message(chat_id, status_text)

    return "ok", 200

@app.route("/")
def index():
    """Página de inicio simple para verificar que la app está viva."""
    return "Bot is alive!", 200