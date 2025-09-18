import os
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ConversationHandler, ContextTypes
)
import threading

# Importamos las nuevas funciones refactorizadas
from core_generator import find_relevant_topic, generate_tweet_from_topic

# Cargar configuración
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Estados para la conversación
AWAITING_APPROVAL = 1

# --- Funciones del Bot ---

def generate_and_propose(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Función principal que genera y envía la propuesta al usuario."""
    
    # 1. Busca un tema relevante (si no hay uno ya en contexto)
    if 'current_topic' not in context.user_data:
        context.bot.send_message(chat_id, "🤖 Buscando un nuevo tema relevante...")
        topic = find_relevant_topic()
        if not topic:
            context.bot.send_message(chat_id, "❌ No pude encontrar un tema relevante en la base de datos.")
            return ConversationHandler.END
        context.user_data['current_topic'] = topic
    
    topic = context.user_data['current_topic']
    topic_abstract = topic.get("abstract")

    context.bot.send_message(chat_id, f"✍️ Tema: '{topic_abstract}'.\nGenerando borrador...")
    
    # 2. Genera el tuit a partir del tema
    eng_tweet, spa_tweet = generate_tweet_from_topic(topic_abstract)
    
    if "Error:" in eng_tweet:
        context.bot.send_message(chat_id, f"Hubo un problema: {eng_tweet}")
        return ConversationHandler.END

    # Almacena los tuits generados en el contexto para usarlos después
    context.user_data['eng_tweet'] = eng_tweet
    context.user_data['spa_tweet'] = spa_tweet
    
    # 3. Construye el mensaje con los botones
    keyboard = [
        [
            InlineKeyboardButton("👍 Aprobar", callback_data='approve'),
            InlineKeyboardButton("👎 Rechazar", callback_data='reject'),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # 4. Envía la propuesta
    message_text = f"**Borrador Propuesto:**\n\n**EN:**\n{eng_tweet}\n\n**ES:**\n{spa_tweet}\n\n¿Aprobar para la cola de publicación?"
    context.bot.send_message(chat_id, text=message_text, reply_markup=reply_markup, parse_mode='Markdown')
    
    return AWAITING_APPROVAL

def approval_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Maneja la respuesta del usuario (botones 👍/👎)."""
    query = update.callback_query
    query.answer() # Responde al callback para que el botón deje de cargar
    
    if query.data == 'approve':
        query.edit_message_text(text="✅ **¡Tuit Aprobado!**\n\n(Próximamente se añadirá a la cola de publicación)")
        # Limpia el contexto para la próxima vez
        context.user_data.clear()
        return ConversationHandler.END
        
    elif query.data == 'reject':
        query.edit_message_text(text="❌ **Borrador Rechazado.**\nGenerando una nueva versión sobre el mismo tema. Un momento...")
        # Llama de nuevo a la función de generación, que reutilizará el tema actual
        return generate_and_propose(update.effective_chat.id, context)

# --- Comandos del Bot ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Mensaje de bienvenida."""
    await update.message.reply_text("¡Hola! Soy tu ghostwriter. Envíame /generate para empezar.")

async def generate_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Inicia la conversación para generar un tuit."""
    chat_id = update.effective_chat.id
    print(f"Recibido /generate del chat_id: {chat_id}")
    
    # Limpia datos de conversaciones anteriores
    context.user_data.clear()
    
    # Usamos un thread para no bloquear el bot mientras la IA trabaja
    threading.Thread(target=generate_and_propose, args=(chat_id, context)).start()
    
    return AWAITING_APPROVAL

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancela la conversación actual."""
    await update.message.reply_text("Operación cancelada.")
    context.user_data.clear()
    return ConversationHandler.END

def main() -> None:
    """Inicia el bot."""
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('generate', generate_command)],
        states={
            AWAITING_APPROVAL: [CallbackQueryHandler(approval_handler)],
        },
        fallbacks=[CommandHandler('cancel', cancel_command)],
    )

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(conv_handler)

    print("🚀 El bot conversacional está en línea...")
    application.run_polling()

if __name__ == "__main__":
    main()