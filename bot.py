import os
import threading
from typing import Dict, Optional

from dotenv import load_dotenv
from flask import Flask, request

from admin_service import AdminService
from draft_repository import DraftRepository
from logger_config import logger
from proposal_service import ProposalService
from telegram_client import TelegramClient

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SHOW_TOPIC_ID = os.getenv("SHOW_TOPIC_ID", "0").lower() in ("1", "true", "yes", "y")
ADMIN_API_TOKEN = os.getenv("ADMIN_API_TOKEN", "")
SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", "0.20") or 0.20)
TEMP_DIR = os.getenv("BOT_TEMP_DIR", "/tmp")

app = Flask(__name__)
telegram_client = TelegramClient(TELEGRAM_BOT_TOKEN, show_topic_id=SHOW_TOPIC_ID)
draft_repo = DraftRepository(TEMP_DIR)
proposal_service = ProposalService(
    telegram=telegram_client,
    draft_repo=draft_repo,
    similarity_threshold=SIMILARITY_THRESHOLD,
)
admin_service = AdminService()


# ---------------------------------------------------------------------- helpers
def _send_pdf_summary(chat_id: int) -> None:
    try:
        stats = admin_service.collect_pdf_stats()
        message = admin_service.build_pdf_summary_message(stats)
        telegram_client.send_message(chat_id, message, as_html=True)
    except Exception as exc:
        logger.error("[CHAT_ID: %s] Error en /pdfs: %s", chat_id, exc, exc_info=True)
        telegram_client.send_message(chat_id, "❌ No pude consultar la base de datos.")


def _chat_has_token(token: Optional[str]) -> bool:
    return not ADMIN_API_TOKEN or token == ADMIN_API_TOKEN


# ---------------------------------------------------------------------- routes
@app.route(f"/{TELEGRAM_BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    update: Dict = request.get_json()
    if "message" in update:
        message = update["message"]
        text = (message.get("text") or "").strip()
        chat_id = message["chat"]["id"]

        if text == "/generate":
            logger.info("[CHAT_ID: %s] Comando '/generate' recibido.", chat_id)
            threading.Thread(target=proposal_service.do_the_work, args=(chat_id,)).start()
        elif text.startswith("/pdfs"):
            logger.info("[CHAT_ID: %s] Comando '/pdfs' recibido.", chat_id)
            _send_pdf_summary(chat_id)
        else:
            telegram_client.send_message(chat_id, "Comando no reconocido. Usa /generate para obtener propuestas.")
    elif "callback_query" in update:
        proposal_service.handle_callback_query(update)
    return "ok", 200


@app.route("/")
def index():
    return "Bot is alive!", 200


@app.route("/stats")
def stats():
    token = request.args.get("token", "")
    if not _chat_has_token(token):
        return {"ok": False, "error": "forbidden"}, 403
    return {"ok": True, **admin_service.get_stats()}, 200


@app.route("/pdfs")
def pdfs_stats():
    token = request.args.get("token", "")
    if not _chat_has_token(token):
        return {"ok": False, "error": "forbidden"}, 403
    try:
        stats = admin_service.collect_pdf_stats()
        return {"ok": True, **stats}, 200
    except Exception:
        return {"ok": False, "error": "server_error"}, 500


@app.route("/ingest_topics", methods=["POST"])
def ingest_topics():
    token = request.args.get("token", "")
    if not _chat_has_token(token):
        return {"ok": False, "error": "forbidden"}, 403

    try:
        data = request.get_json(force=True, silent=False) or {}
        items = data.get("topics", [])
        if not isinstance(items, list) or not items:
            return {"ok": False, "error": "empty_payload"}, 400
        added, skipped_existing, errors = admin_service.ingest_topics(items)
        return {
            "ok": True,
            "received": len(items),
            "added": added,
            "skipped_existing": skipped_existing,
            "errors": errors,
        }, 200
    except Exception as exc:
        logger.error("/ingest_topics failed: %s", exc, exc_info=True)
        return {"ok": False, "error": "server_error"}, 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
