print("---- ESTA ES LA PUTA VERSIÓN NUEVA DEL CÓDIGO: v_FINAL ----", flush=True)

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from queue import Full, Queue
from typing import Any, Callable, Dict, Optional

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
TELEGRAM_SECRET_TOKEN = os.getenv("TELEGRAM_SECRET_TOKEN", "")
SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", "0.20") or 0.20)
TEMP_DIR = os.getenv("BOT_TEMP_DIR", "/tmp")
JOB_MAX_WORKERS = int(os.getenv("JOB_MAX_WORKERS", "3") or 3)
JOB_QUEUE_MAXSIZE = int(os.getenv("JOB_QUEUE_MAXSIZE", "12") or 12)
JOB_TIMEOUT_SECONDS = float(os.getenv("JOB_TIMEOUT_SECONDS", "35") or 35.0)


app = Flask(__name__)
telegram_client = TelegramClient(TELEGRAM_BOT_TOKEN, show_topic_id=SHOW_TOPIC_ID)
draft_repo = DraftRepository(TEMP_DIR)
proposal_service = ProposalService(
    telegram=telegram_client,
    draft_repo=draft_repo,
    similarity_threshold=SIMILARITY_THRESHOLD,
)
admin_service = AdminService()

_job_executor = ThreadPoolExecutor(max_workers=JOB_MAX_WORKERS)
_job_queue: "Queue[Dict[str, Any]]" = Queue(maxsize=JOB_QUEUE_MAXSIZE)
_chat_locks: Dict[int, threading.Lock] = {}
_chat_lock_guard = threading.Lock()


def _acquire_chat_lock(chat_id: int) -> bool:
    with _chat_lock_guard:
        lock = _chat_locks.get(chat_id)
        if lock is None:
            lock = threading.Lock()
            _chat_locks[chat_id] = lock
    acquired = lock.acquire(blocking=False)
    if not acquired:
        logger.info("[CHAT_ID: %s] Job ya en curso; se evita duplicado.", chat_id)
    return acquired


def _release_chat_lock(chat_id: int) -> None:
    with _chat_lock_guard:
        lock = _chat_locks.get(chat_id)
    if lock and lock.locked():
        lock.release()


def _run_job(job: Dict[str, Any]) -> None:
    chat_id = job["chat_id"]
    func: Callable[..., None] = job["func"]
    args = job.get("args", ())
    kwargs = job.get("kwargs", {})
    try:
        func(*args, **kwargs)
    except Exception as exc:
        logger.error("[CHAT_ID: %s] Error ejecutando job: %s", chat_id, exc, exc_info=True)
        try:
            telegram_client.send_message(chat_id, "❌ Ocurrió un error inesperado al generar la propuesta.")
        except Exception:
            logger.exception("[CHAT_ID: %s] No se pudo notificar el error al usuario.", chat_id)
    finally:
        _release_chat_lock(chat_id)
        _job_queue.task_done()


def _job_dispatcher() -> None:
    while True:
        job = _job_queue.get()
        if job is None:
            _job_queue.task_done()
            break
        _job_executor.submit(_run_job, job)


_dispatcher_thread = threading.Thread(target=_job_dispatcher, daemon=True)
_dispatcher_thread.start()


def _schedule_generation(chat_id: int) -> None:
    if not _acquire_chat_lock(chat_id):
        telegram_client.send_message(chat_id, "⚠️ Ya estoy trabajando en tu última solicitud. Dame unos segundos.")
        return
    job = {
        "chat_id": chat_id,
        "func": proposal_service.do_the_work,
        "args": (chat_id,),
        "kwargs": {"deadline": time.monotonic() + JOB_TIMEOUT_SECONDS},
    }
    try:
        _job_queue.put_nowait(job)
        logger.info(
            "[CHAT_ID: %s] Job encolado (queue=%s/%s).",
            chat_id,
            _job_queue.qsize(),
            JOB_QUEUE_MAXSIZE,
        )
    except Full:
        _release_chat_lock(chat_id)
        logger.warning("[CHAT_ID: %s] Cola de trabajos llena. Rechazando solicitud.", chat_id)
        telegram_client.send_message(chat_id, "⌛ Estoy al máximo ahora mismo. Intenta en breve.")


proposal_service.job_scheduler = _schedule_generation


# ---------------------------------------------------------------------- helpers
def _process_update(update: Dict) -> None:
    logger.info("Update recibido: %s", update)
    if "message" in update:
        message = update["message"]
        text = (message.get("text") or "").strip()
        chat_id = message["chat"]["id"]

        if text == "/g":
            logger.info("[CHAT_ID: %s] Comando '/g' recibido.", chat_id)
            _schedule_generation(chat_id)
            return
        elif text.startswith("/c"):
            logger.info("[CHAT_ID: %s] Comando '/c' recibido.", chat_id)
            payload = text[len("/c"):].strip()
            threading.Thread(target=proposal_service.generate_comment, args=(chat_id, payload)).start()
        elif text.startswith("/pdfs"):
            logger.info("[CHAT_ID: %s] Comando '/pdfs' recibido.", chat_id)
            _send_pdf_summary(chat_id)
        elif text == "/ping":
            logger.info("[CHAT_ID: %s] Comando '/ping' recibido.", chat_id)
            try:
                import requests
                url = os.getenv("CHROMA_DB_URL")
                if not url:
                    telegram_client.send_message(chat_id, "❌ CHROMA_DB_URL no está configurada.")
                    return
                
                # Asegurarse de que la URL tiene el endpoint correcto
                if not url.endswith('/'):
                    url += '/'
                heartbeat_url = f"{url}api/v1/heartbeat"
                
                logger.info(f"Haciendo ping a la base de datos en: {heartbeat_url}")
                response = requests.get(heartbeat_url, timeout=10)
                response.raise_for_status()
                telegram_client.send_message(chat_id, f"✅ Ping exitoso. Respuesta: {response.json()}")
            except Exception as e:
                logger.error(f"[CHAT_ID: {chat_id}] Error en /ping: {e}", exc_info=True)
                telegram_client.send_message(chat_id, f"❌ Ping fallido: {e}")
        else:
            telegram_client.send_message(chat_id, "Comando no reconocido. Usa /generate para obtener propuestas.")
    elif "callback_query" in update:
        proposal_service.handle_callback_query(update)


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
    _process_update(update)
    return "ok", 200


@app.route("/webhook", methods=["POST"])
def telegram_webhook_alt():
    # Opción alternativa sin exponer el token en la ruta.
    # Si TELEGRAM_SECRET_TOKEN está configurado, exigimos el header estándar de Telegram.
    if TELEGRAM_SECRET_TOKEN:
        header_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if header_token != TELEGRAM_SECRET_TOKEN:
            return {"ok": False, "error": "forbidden"}, 403
    update: Dict = request.get_json()
    _process_update(update)
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
# Force re-deploy
