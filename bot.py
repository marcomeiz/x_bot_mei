import os
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import asyncio

# Importamos la función que hace todo el trabajo pesado
from core_generator import generate_single_tweet

# Cargar el token del bot desde el archivo .env
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN no encontrado en el archivo .env")

# --- Definición de Comandos para el Bot ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Envía un mensaje de bienvenida cuando el usuario escribe /start."""
    await update.message.reply_text("¡Hola! Soy tu ghostwriter. Envíame /generate para crear un nuevo tuit.")

async def generate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manejador para el comando /generate."""
    chat_id = update.effective_chat.id
    print(f"Recibido comando /generate del chat_id: {chat_id}")

    # Enviar un mensaje de "trabajando en ello" para una mejor experiencia de usuario
    await update.message.reply_text("🤖 Entendido. Buscando un tema relevante y empezando el proceso creativo... Esto puede tardar un minuto.")

    # Ejecutar la función de generación en un hilo separado para no bloquear el bot
    loop = asyncio.get_running_loop()
    eng_tweet, spa_tweet = await loop.run_in_executor(None, generate_single_tweet)

    # Enviar los resultados al usuario
    if "Error:" in eng_tweet:
        await update.message.reply_text(f"Hubo un problema: {eng_tweet}")
    else:
        # Usamos la función send_telegram_message que ya teníamos, pero la adaptamos para el bot
        if eng_tweet:
            await context.bot.send_message(chat_id=chat_id, text=f"**English Version Approved:**\n\n{eng_tweet}", parse_mode='Markdown')
        if spa_tweet:
            await asyncio.sleep(1) # Pequeña pausa
            await context.bot.send_message(chat_id=chat_id, text=f"**Versión en Español Aprobada:**\n\n{spa_tweet}", parse_mode='Markdown')

def main() -> None:
    """Inicia el bot y lo mantiene escuchando."""
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Registrar los manejadores de comandos
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("generate", generate))

    print("🚀 El bot está en línea y escuchando comandos...")
    # Iniciar el bot (se quedará escuchando hasta que lo detengas con Ctrl+C)
    application.run_polling()

if __name__ == "__main__":
    main()