"""Simple utilities to send alerts (currently Telegram only)."""

from __future__ import annotations

import os
from typing import Optional

from logger_config import logger
from telegram_client import TelegramClient


def send_telegram_message(message: str, parse_mode: str = "HTML") -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id_raw = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id_raw:
        logger.info("Telegram no configurado; omitiendo notificación.")
        return False

    try:
        chat_id = int(chat_id_raw)
    except ValueError:
        logger.error("TELEGRAM_CHAT_ID inválido: %s", chat_id_raw)
        return False

    client = TelegramClient(bot_token=token)
    ok = client.send_message(chat_id=chat_id, text=message, as_html=parse_mode == "HTML")
    if ok:
        logger.info("Notificación Telegram enviada correctamente.")
    return ok
