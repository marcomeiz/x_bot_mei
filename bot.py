import os
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ConversationHandler, ContextTypes
)
import threading

# Importamos las funciones refactorizadas
from core_generator import find_relevant_topic, generate_tweet_from_topic

# Cargar configuración
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Estados para la conversación
AWAITING_APPROVAL = 1

# --- Teclado para "Generar Nuevo" ---
def get_new_tweet_keyboard():
    """Crea el teclado con el botón para generar un nuevo tuit."""
    keyboard = [[InlineKeyboardButton("🚀 Generar Nuevo Tuit", callback_data='generate_new')]]
    return InlineKeyboardMarkup(keyboard)

# --- Funciones del Bot ---

def generate_and_propose(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Función principal que genera y envía la propuesta al usuario."""
    
    if 'current_topic' not in context.user_data:
        context.bot.send_message(chat_id, "🤖 Buscando un nuevo tema relevante...")
        topic = find_relevant_topic()
        if not topic:
            context.bot.send_message(
                chat_id,
                "❌ No pude encontrar un tema relevante en la base de datos.",
                reply_markup=get_new_tweet_keyboard()
            )
            return
        context.user_data['current_topic'] = topic
    
    topic = context.user_data['current_topic']
    topic_abstract = topic.get("abstract")

    context.bot.send_message(chat_id, f"✍️ Tema: '{topic_abstract}'.\nGenerando borrador...")
    
    eng_tweet, spa_tweet = generate_tweet_from_topic(topic_abstract)
    
    if "Error:" in eng_tweet:
        context.bot.send_message(
            chat_id,
            f"Hubo un problema: {eng_tweet}",
            reply_markup=get_new_tweet_keyboard()
        )
        return

    context.user_data['eng_tweet'] = eng_tweet
    context.user_data['spa_tweet'] = spa_tweet
    
    keyboard = [
        [
            InlineKeyboardButton("👍 Aprobar", callback_data='approve'),
            InlineKeyboardButton("👎 Rechazar", callback_data='reject'),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    message_text = f"**Borrador Propuesto:**\n\n**EN:**\n{eng_tweet}\n\n**ES:**\n{spa_tweet}\n\n¿Aprobar para la cola de publicación?"
    context.bot.send_message(chat_id, text=message_text, reply_markup=reply_markup, parse_mode='Markdown')

# --- Manejadores de Estados y Comandos ---

async def approval_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Maneja la respuesta del usuario (botones 👍/👎)."""
    query = update.callback_query
    await query.answer()
    
    if query.data == 'approve':
        await query.edit_message_text(
            text="✅ **¡Tuit Aprobado!**\n\n(Próximamente se añadirá a la cola de publicación)",
            reply_markup=get_new_tweet_keyboard() # Añadimos el botón de generar nuevo
        )
        context.user_data.clear()
        return ConversationHandler.END
        
    elif query.data == 'reject':
        await query.edit_message_text(text="❌ **Borrador Rechazado.**\nGenerando una nueva versión sobre el mismo tema. Un momento...")
        threading.Thread(target=generate_and_propose, args=(update.effective_chat.id, context)).start()
        return AWAITING_APPROVAL # Mantenemos el estado de espera

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Mensaje de bienvenida."""
    await update.message.reply_text(
        "¡Hola! Soy tu ghostwriter.",
        reply_markup=get_new_tweet_keyboard()
    )

async def generate_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Inicia la conversación para generar un tuit, ya sea por comando o por botón."""
    chat_id = update.effective_chat.id
    
    # Si viene de un botón, respondemos al callback
    if update.callback_query:
        await update.callback_query.answer()
        # Opcional: editar el mensaje anterior para quitar el botón
        await update.callback_query.edit_message_reply_markup(reply_markup=None)

    print(f"Iniciando generación para el chat_id: {chat_id}")
    context.user_data.clear()
    
    # El trabajo pesado se hace en un hilo para no bloquear
    threading.Thread(target=generate_and_propose, args=(chat_id, context)).start()
    
    return AWAITING_APPROVAL

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancela la conversación actual."""
    await update.message.reply_text(
        "Operación cancelada.",
        reply_markup=get_new_tweet_keyboard()
    )
    context.user_data.clear()
    return ConversationHandler.END

def main() -> None:
    """Inicia el bot."""
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler('generate', generate_command),
            CallbackQueryHandler(generate_command, pattern='^generate_new$') # El botón ahora llama a la misma función
        ],
        states={
            AWAITING_APPROVAL: [CallbackQueryHandler(approval_handler, pattern='^(approve|reject)$')],
        },
        fallbacks=[CommandHandler('cancel', cancel_command)],
    )

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(conv_handler)

    print("🚀 El bot conversacional con botón de regeneración está en línea...")
    application.run_polling()

if __name__ == "__main__":
    main()