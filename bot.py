import logging_bootstrap  # <-- inicialización temprana de Cloud Logging
print("[SYSTEM] X Bot Mei v2.0 - Production Build Initialized", flush=True)

import os
import re
import math
import threading
from concurrent.futures import ThreadPoolExecutor
from queue import Full, Queue
from typing import Any, Callable, Dict, Optional

from dotenv import load_dotenv
from flask import Flask, request

from admin_service import AdminService
from analytics import analytics
from draft_repository import DraftRepository
from logger_config import logger
from proposal_service import ProposalService
from src.messages import get_message
from telegram_client import TelegramClient

# Warmup/health imports
from embeddings_manager import get_chroma_client, get_topics_collection, get_embedding
from src.topics_repo import get_health as get_topics_health

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SHOW_TOPIC_ID = os.getenv("SHOW_TOPIC_ID", "0").lower() in ("1", "true", "yes", "y")
ADMIN_API_TOKEN = os.getenv("ADMIN_API_TOKEN", "")
TELEGRAM_SECRET_TOKEN = os.getenv("TELEGRAM_SECRET_TOKEN", "")
SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", "0.20") or 0.20)
TEMP_DIR = os.getenv("BOT_TEMP_DIR", "/tmp")
JOB_MAX_WORKERS = int(os.getenv("JOB_MAX_WORKERS", "3") or 3)
JOB_QUEUE_MAXSIZE = int(os.getenv("JOB_QUEUE_MAXSIZE", "12") or 12)

# User access control (whitelist)
ALLOWED_USER_IDS_STR = os.getenv("ALLOWED_USER_IDS", "")
ALLOWED_USER_IDS = set(int(uid.strip()) for uid in ALLOWED_USER_IDS_STR.split(",") if uid.strip())
BOT_PRIVATE = len(ALLOWED_USER_IDS) > 0  # If whitelist is configured, bot is private
logger.info(f"Bot access mode: {'PRIVATE' if BOT_PRIVATE else 'PUBLIC'} (allowed users: {len(ALLOWED_USER_IDS)})")
# NOTE: JOB_TIMEOUT_SECONDS intentionally unused while we debug long-running generations.

# Health/warmup envs
SIM_DIM = int(os.getenv("SIM_DIM", "3072") or 3072)
WARMUP_ANCHORS = int(os.getenv("WARMUP_ANCHORS", "200") or 200)
TOPICS_COLLECTION_NAME = os.getenv("TOPICS_COLLECTION", "topics_collection")
UMBRAL_SIMILITUD_LINE = float(os.getenv("UMBRAL_SIMILITUD_LINE", "0.52") or 0.52)
NOVELTY_JACCARD_MAX = float(os.getenv("NOVELTY_JACCARD_MAX", "0.28") or 0.28)
NOVELTY_COS_CEILING = float(os.getenv("NOVELTY_COS_CEILING", "0.84") or 0.84)
STYLE_MIN = float(os.getenv("STYLE_MIN", "0.90") or 0.90)

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

# Warmup cache
_ANCHORS_CACHE: list[Dict[str, Any]] = []


def _warmup_anchors() -> int:
    """Carga anclas (goldset y, si falta, topics) y pre-computa embeddings a memoria."""
    try:
        client = get_chroma_client()
        # Goldset primero
        gold = client.get_or_create_collection("goldset_collection", metadata={"hnsw:space": "cosine"})
        res = gold.get(include=["documents", "embeddings"]) or {}
        g_docs = res.get("documents") or []
        g_vecs = res.get("embeddings") or []
        for d, e in zip(g_docs, g_vecs):
            text = d[0] if isinstance(d, list) else d
            _ANCHORS_CACHE.append({"text": text, "vec": e})
        # Si faltan, completar con topics usando embeddings ya calculados
        if len(_ANCHORS_CACHE) < WARMUP_ANCHORS:
            topics = get_topics_collection()
            t_res = topics.get(include=["documents", "embeddings"]) or {}
            t_docs = t_res.get("documents") or []
            t_vecs = t_res.get("embeddings") or []
            need = max(0, WARMUP_ANCHORS - len(_ANCHORS_CACHE))
            added = 0
            for d, e in zip(t_docs, t_vecs):
                if added >= need:
                    break
                if e and isinstance(e, list) and e:
                    text = d[0] if isinstance(d, list) else d
                    _ANCHORS_CACHE.append({"text": text, "vec": e})
                    added += 1
            # No generamos embeddings en caliente aquí para evitar costos y duplicación.
            # Si faltan, simplemente reflejamos warmed < WARMUP_ANCHORS y el endpoint de health lo mostrará.
            if added < need:
                logger.info("Warmup: insuficientes embeddings precomputados en topics; no se generarán en caliente (faltan=%s).", need - added)
        warmed = len(_ANCHORS_CACHE)
        logger.info("warmup_ok=%s, warmed=%s", warmed >= WARMUP_ANCHORS, warmed)
        return warmed
    except Exception as e:
        logger.error("Warmup failed: %s", e, exc_info=True)
        return 0

# warmup en background
threading.Thread(target=_warmup_anchors, daemon=True).start()


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
    import time
    chat_id = job["chat_id"]
    func: Callable[..., None] = job["func"]
    args = job.get("args", ())
    kwargs = job.get("kwargs", {})
    user_id = job.get("user_id")
    model = job.get("model", "unknown")
    job_type = job.get("type", "generation")  # "generation" or "comment"

    start_time = time.time()
    try:
        func(*args, **kwargs)
        # Track successful job
        response_time = time.time() - start_time
        if job_type == "generation" and user_id:
            analytics.track_generation(user_id, model, response_time)
        elif job_type == "comment" and user_id:
            analytics.track_comment(user_id, response_time)
    except Exception as exc:
        logger.error("[CHAT_ID: %s] Error ejecutando job: %s", chat_id, exc, exc_info=True)
        # Track error
        if user_id:
            analytics.track_error(user_id, type(exc).__name__, job_type)
        try:
            telegram_client.send_message(chat_id, get_message("generate_error"), as_html=True)
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


def _schedule_generation(chat_id: int, user_id: int = None, model_override: Optional[str] = None) -> None:
    if not _acquire_chat_lock(chat_id):
        telegram_client.send_message(chat_id, get_message("queue_busy"))
        return

    # Get model name for tracking
    from src.settings import AppSettings
    settings = AppSettings.load()
    model_name = model_override or settings.post_model or "google/gemini-2.0-flash-exp"

    job = {
        "chat_id": chat_id,
        "user_id": user_id,
        "model": model_name,
        "type": "generation",
        "func": proposal_service.do_the_work,
        "args": (chat_id,),
        "kwargs": {"model_override": model_override} if model_override else {},
    }
    try:
        _job_queue.put_nowait(job)
        logger.info(
            "[CHAT_ID: %s] Job encolado (queue=%s/%s). Model: %s",
            chat_id,
            _job_queue.qsize(),
            JOB_QUEUE_MAXSIZE,
            model_override or "default",
        )
    except Full:
        _release_chat_lock(chat_id)
        logger.warning("[CHAT_ID: %s] Cola de trabajos llena. Rechazando solicitud.", chat_id)
        telegram_client.send_message(chat_id, get_message("queue_full"))


proposal_service.job_scheduler = _schedule_generation


# ---------------------------------------------------------------------- helpers
def _process_update(update: Dict) -> None:
    logger.info("Update recibido: %s", update)
    if "message" in update:
        message = update["message"]
        text = (message.get("text") or "").strip()
        chat_id = message["chat"]["id"]
        user_id = message["from"]["id"]

        # Access control check
        if not _is_user_allowed(user_id):
            _send_access_denied(chat_id, user_id)
            analytics.track_command("DENIED", user_id)
            return

        # Track command
        command = text.split()[0] if text else "unknown"
        analytics.track_command(command, user_id)

        if text == "/start":
            logger.info("[CHAT_ID: %s] Comando '/start' recibido.", chat_id)
            telegram_client.send_message(chat_id, get_message("start_welcome"), as_html=True)
            return
        elif text == "/help":
            logger.info("[CHAT_ID: %s] Comando '/help' recibido.", chat_id)
            telegram_client.send_message(chat_id, get_message("help_message"), as_html=True)
            return
        elif text == "/g":
            logger.info("[CHAT_ID: %s] Comando '/g' recibido (Claude Sonnet 4.5).", chat_id)
            _schedule_generation(chat_id, user_id=user_id, model_override="anthropic/claude-sonnet-4.5")
            return
        elif text.startswith("/c"):
            logger.info("[CHAT_ID: %s] Comando '/c' recibido.", chat_id)
            payload = text[len("/c"):].strip()
            threading.Thread(target=proposal_service.generate_comment, args=(chat_id, payload)).start()
        elif text.startswith("/pdfs"):
            logger.info("[CHAT_ID: %s] Comando '/pdfs' recibido.", chat_id)
            _send_pdf_summary(chat_id)
        elif text.startswith("/tema "):
            logger.info("[CHAT_ID: %s] Comando '/tema' recibido.", chat_id)
            abstract = text[6:].strip()  # Remove "/tema "
            threading.Thread(target=_handle_add_topic, args=(chat_id, abstract)).start()
        elif text == "/temas":
            logger.info("[CHAT_ID: %s] Comando '/temas' recibido.", chat_id)
            threading.Thread(target=_handle_list_topics, args=(chat_id,)).start()
        elif text == "/ping":
            logger.info("[CHAT_ID: %s] Comando '/ping' recibido.", chat_id)
            try:
                import requests
                url = os.getenv("CHROMA_DB_URL")
                if not url:
                    telegram_client.send_message(chat_id, get_message("chroma_missing"))
                    return

                # Asegurarse de que la URL tiene el endpoint correcto
                if not url.endswith('/'):
                    url += '/'
                heartbeat_url = f"{url}api/v1/heartbeat"

                logger.info(f"Haciendo ping a la base de datos en: {heartbeat_url}")
                response = requests.get(heartbeat_url, timeout=10)
                response.raise_for_status()
                telegram_client.send_message(chat_id, get_message("ping_success", response=response.json()))
            except Exception as e:
                logger.error(f"[CHAT_ID: {chat_id}] Error en /ping: {e}", exc_info=True)
                telegram_client.send_message(chat_id, get_message("ping_failure", error=e))
        else:
            telegram_client.send_message(chat_id, get_message("unknown_command"))
    elif "callback_query" in update:
        proposal_service.handle_callback_query(update)


def _send_pdf_summary(chat_id: int) -> None:
    try:
        stats = admin_service.collect_pdf_stats()
        message = admin_service.build_pdf_summary_message(stats)
        telegram_client.send_message(chat_id, message, as_html=True)
    except Exception as exc:
        logger.error("[CHAT_ID: %s] Error en /pdfs: %s", chat_id, exc, exc_info=True)
        telegram_client.send_message(chat_id, get_message("db_query_error"))


def _handle_add_topic(chat_id: int, abstract: str) -> None:
    """Maneja el comando /tema para agregar un nuevo tema."""
    try:
        from topic_manager import add_topic, get_topics_count

        # Send processing message
        telegram_client.send_message(chat_id, "🔄 Agregando tema...")

        # Add topic
        result = add_topic(abstract, source='telegram', approved=False)

        if result['success']:
            total_count = get_topics_count()
            message = f"{result['message']}\n\n📊 Total de temas: {total_count}"
            telegram_client.send_message(chat_id, message)
        else:
            telegram_client.send_message(chat_id, f"❌ {result['message']}")

    except Exception as e:
        logger.error(f"[CHAT_ID: {chat_id}] Error agregando tema: {e}", exc_info=True)
        telegram_client.send_message(chat_id, f"❌ Error agregando tema: {e}")


def _handle_list_topics(chat_id: int) -> None:
    """Maneja el comando /temas para listar temas recientes."""
    try:
        from topic_manager import list_recent_topics, get_topics_count

        total = get_topics_count()
        recent = list_recent_topics(limit=10)

        if not recent:
            telegram_client.send_message(chat_id, "📭 No hay temas en la base de datos")
            return

        lines = [f"📚 <b>Últimos {len(recent)} temas</b> (total: {total})\n"]

        for topic in recent:
            topic_id = topic['id']
            abstract = topic['abstract'][:80] + "..." if len(topic['abstract']) > 80 else topic['abstract']
            source = topic.get('source', 'unknown')

            lines.append(f"• <code>{topic_id}</code>")
            lines.append(f"  {abstract}")
            lines.append(f"  <i>Fuente: {source}</i>\n")

        message = "\n".join(lines)
        telegram_client.send_message(chat_id, message, as_html=True)

    except Exception as e:
        logger.error(f"[CHAT_ID: {chat_id}] Error listando temas: {e}", exc_info=True)
        telegram_client.send_message(chat_id, f"❌ Error listando temas: {e}")


def _is_user_allowed(user_id: int) -> bool:
    """Check if user is allowed to use the bot."""
    if not BOT_PRIVATE:
        return True  # Public mode, everyone allowed
    return user_id in ALLOWED_USER_IDS


def _send_access_denied(chat_id: int, user_id: int):
    """Send access denied message to unauthorized user."""
    message = f"""🔒 <b>Acceso Denegado</b>

Este bot es privado y solo puede ser usado por usuarios autorizados.

<b>Tu User ID:</b> <code>{user_id}</code>

Si crees que deberías tener acceso, contacta al administrador."""
    telegram_client.send_message(chat_id, message, as_html=True)
    logger.warning(f"Access denied for user {user_id} (chat {chat_id})")


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


@app.route("/analytics")
def analytics_endpoint():
    """
    Analytics endpoint showing bot usage metrics.
    Protected with ADMIN_API_TOKEN.

    Usage: GET /analytics?token=YOUR_ADMIN_API_TOKEN
    """
    token = request.args.get("token", "")
    if not _chat_has_token(token):
        return {"ok": False, "error": "forbidden"}, 403

    try:
        stats = analytics.get_stats()
        return {"ok": True, "analytics": stats}, 200
    except Exception as e:
        logger.error(f"Error getting analytics: {e}", exc_info=True)
        return {"ok": False, "error": "server_error"}, 500


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


@app.route("/health/embeddings")
def health_embeddings():
    """Devuelve salud de embeddings y acuerdo de dimensiones.

    ok = True solo si:
    - goldset_dim, topics_dim y memory_dim coinciden entre sí y (si está definida) con SIM_DIM
    - warmed >= WARMUP_ANCHORS
    """
    try:
        client = get_chroma_client()
        gold = client.get_or_create_collection("goldset_collection", metadata={"hnsw:space": "cosine"})
        g_res = gold.get(include=["embeddings"], limit=200) or {}
        g_vecs = g_res.get("embeddings") or []
        gold_dims_seen = sorted({(len(v) if isinstance(v, list) else None) for v in g_vecs if isinstance(v, list) and v})
        gold_dim = (gold_dims_seen[0] if gold_dims_seen else None)

        topics = client.get_or_create_collection(TOPICS_COLLECTION_NAME, metadata={"hnsw:space": "cosine"})
        t_res = topics.get(include=["embeddings"], limit=200) or {}
        t_vecs = t_res.get("embeddings") or []
        topics_dims_seen = sorted({(len(v) if isinstance(v, list) else None) for v in t_vecs if isinstance(v, list) and v})
        topics_dim = (topics_dims_seen[0] if topics_dims_seen else None)

        memory = client.get_or_create_collection("memory_collection", metadata={"hnsw:space": "cosine"})
        m_res = memory.get(include=["embeddings"], limit=200) or {}
        m_vecs = m_res.get("embeddings") or []
        memory_dims_seen = sorted({(len(v) if isinstance(v, list) else None) for v in m_vecs if isinstance(v, list) and v})
        memory_dim = (memory_dims_seen[0] if memory_dims_seen else None)

        warmed = len(_ANCHORS_CACHE)
        sim_dim_env = SIM_DIM if SIM_DIM > 0 else None

        dims_present = [d for d in (gold_dim, topics_dim, memory_dim) if d is not None]
        all_equal = len(set(dims_present)) == 1 if dims_present else True
        agree_with_env = (sim_dim_env is None) or (not dims_present) or (dims_present[0] == sim_dim_env)
        agree_dim = bool(all_equal and agree_with_env)

        ok = bool(agree_dim and (warmed >= WARMUP_ANCHORS))

        payload = {
            "goldset_dim": gold_dim,
            "topics_dim": topics_dim,
            "memory_dim": memory_dim,
            "goldset_dims_seen": gold_dims_seen,
            "topics_dims_seen": topics_dims_seen,
            "memory_dims_seen": memory_dims_seen,
            "sim_dim_env": sim_dim_env,
            "agree_dim": agree_dim,
            "warmed": warmed,
            "ok": ok,
        }

        if not agree_dim:
            details = []
            details.append(f"goldset={gold_dims_seen or []}")
            details.append(f"topics={topics_dims_seen or []}")
            details.append(f"memory={memory_dims_seen or []}")
            if sim_dim_env is not None:
                details.append(f"SIM_DIM={sim_dim_env}")
            payload["details"] = "; ".join(details)

        return payload, 200
    except Exception:
        return {"ok": False, "error": "server_error"}, 500


@app.route("/collections/topics/health")
def topics_health():
    """Salud de la colección de temas.

    Devuelve 200 siempre con:
    - exists: bool
    - count: int
    - emb_dim: int | None
    """
    try:
        h = get_topics_health()
        # Especificación: devolver solo los campos de salud, sin "ok"
        return {"exists": bool(h.get("exists")), "count": int(h.get("count") or 0), "emb_dim": h.get("emb_dim")}, 200
    except Exception:
        # Degradación controlada: 200 con estado mínimo
        return {"exists": False, "count": 0, "emb_dim": None}, 200


@app.route("/eval/score_line", methods=["POST"])
def eval_score_line():
    try:
        payload = request.get_json(force=True, silent=False) or {}
        draft = str(payload.get("draft") or "")
        anchor_ids = list(payload.get("anchor_ids") or [])
        lines = _split_lines(draft)
        if not lines or not anchor_ids:
            return {"ok": False, "error": "invalid_input"}, 400
        anchors = _fetch_anchors_by_ids(anchor_ids)
        if not anchors:
            return {"ok": False, "error": "anchors_not_found"}, 404
        # embed lines
        line_vecs = []
        for l in lines:
            v = _embed_line(l)
            if not v:
                return {"ok": False, "error": "embedding_failed"}, 500
            line_vecs.append(v)
        # compute s1/s2
        s_per_anchor = []
        for a in anchors[:2]:
            av = _l2_normalize(a["vec"]) if isinstance(a.get("vec"), list) else None
            if not av or len(av) != SIM_DIM:
                return {"ok": False, "error": "anchor_dim_mismatch"}, 500
            s_max = max(_cos(lv, av) for lv in line_vecs)
            s_per_anchor.append(s_max)
        s1 = (s_per_anchor[0] if len(s_per_anchor) > 0 else 0.0)
        s2 = (s_per_anchor[1] if len(s_per_anchor) > 1 else 0.0)
        score_line = max(s1, s2)
        return {"ok": True, "score_line": float(score_line), "s1": float(s1), "s2": float(s2), "lines_draft": len(lines)}, 200
    except Exception as exc:
        logger.error("/eval/score_line failed: %s", exc, exc_info=True)
        return {"ok": False, "error": "server_error"}, 500


@app.route("/eval/full_gate", methods=["POST"])
def eval_full_gate():
    try:
        payload = request.get_json(force=True, silent=False) or {}
        draft = str(payload.get("draft") or "")
        topic = str(payload.get("topic") or "").strip()
        pattern_hint = payload.get("pattern_hint")
        lines = _split_lines(draft)
        if not lines:
            return {"ok": False, "error": "invalid_draft"}, 400
        # Style
        style_score = _style_score(lines)
        # Embed for retrieval/query
        # Average of line embeddings as query vector
        line_vecs = []
        for l in lines:
            v = _embed_line(l)
            if not v:
                return {"ok": False, "error": "embedding_failed"}, 500
            line_vecs.append(v)
        avg_vec = [sum(vals)/len(vals) for vals in zip(*line_vecs)] if line_vecs else None
        # Pattern selection
        pattern_selected = (str(pattern_hint).strip() if isinstance(pattern_hint, str) and pattern_hint.strip() else "diagnostico")
        # Retrieve top-2 anchors by topic+pattern
        anchors = []
        try:
            client = get_chroma_client()
            coll = client.get_or_create_collection(TOPICS_COLLECTION_NAME, metadata={"hnsw:space": "cosine"})
            where = {"pattern": pattern_selected}
            if topic:
                where["topic"] = topic
            q = coll.query(query_embeddings=[avg_vec], where=where, n_results=2, include=["documents", "embeddings", "metadatas", "ids"]) if avg_vec else {}
            ids = (q.get("ids") or [[]])[0]
            docs = (q.get("documents") or [[]])[0]
            embs = (q.get("embeddings") or [[]])[0]
            for id_, d, e in zip(ids or [], docs or [], embs or []):
                text = d[0] if isinstance(d, list) else d
                anchors.append({"id": id_, "text": text, "vec": e})
            # Fallbacks
            if len(anchors) < 2:
                # Try only pattern
                q2 = coll.query(query_embeddings=[avg_vec], where={"pattern": pattern_selected}, n_results=2, include=["documents", "embeddings", "metadatas", "ids"]) if avg_vec else {}
                ids2 = (q2.get("ids") or [[]])[0]
                docs2 = (q2.get("documents") or [[]])[0]
                embs2 = (q2.get("embeddings") or [[]])[0]
                for id_, d, e in zip(ids2 or [], docs2 or [], embs2 or []):
                    text = d[0] if isinstance(d, list) else d
                    anchors.append({"id": id_, "text": text, "vec": e})
            if len(anchors) < 2:
                # No filter
                q3 = coll.query(query_embeddings=[avg_vec], n_results=2, include=["documents", "embeddings", "metadatas", "ids"]) if avg_vec else {}
                ids3 = (q3.get("ids") or [[]])[0]
                docs3 = (q3.get("documents") or [[]])[0]
                embs3 = (q3.get("embeddings") or [[]])[0]
                for id_, d, e in zip(ids3 or [], docs3 or [], embs3 or []):
                    text = d[0] if isinstance(d, list) else d
                    anchors.append({"id": id_, "text": text, "vec": e})
        except Exception:
            anchors = anchors
        anchors = anchors[:2]
        # Score_line
        s_per_anchor = []
        for a in anchors:
            av = _l2_normalize(a["vec"]) if isinstance(a.get("vec"), list) else None
            if not av or len(av) != SIM_DIM:
                continue
            s_max = max(_cos(lv, av) for lv in line_vecs)
            s_per_anchor.append(s_max)
        s1 = (s_per_anchor[0] if len(s_per_anchor) > 0 else 0.0)
        s2 = (s_per_anchor[1] if len(s_per_anchor) > 1 else 0.0)
        score_line = max(s1, s2)
        # Novelty
        jaccs = []
        cos_ceiling_hit = False
        for a in anchors:
            a_tri = _trigrams(a.get("text") or "")
            d_tri = _trigrams("\n".join(lines))
            jaccs.append(_jaccard(a_tri, d_tri))
            av = _l2_normalize(a["vec"]) if isinstance(a.get("vec"), list) else None
            if av:
                for lv in line_vecs:
                    if _cos(lv, av) > NOVELTY_COS_CEILING:
                        cos_ceiling_hit = True
                        break
        jaccard_max = max(jaccs) if jaccs else 0.0
        near_dup = None
        # Gate decisions
        blocked = False
        reasons = []
        if style_score < STYLE_MIN:
            blocked = True
            reasons.append("style")
        if score_line < UMBRAL_SIMILITUD_LINE:
            blocked = True
            reasons.append("similarity")
        if jaccard_max > NOVELTY_JACCARD_MAX or cos_ceiling_hit:
            blocked = True
            reasons.append("novelty")
        after_rewrite = False
        draft_final = "\n".join(lines)
        # Rewrite 1-pass if blocked
        if blocked:
            after_rewrite = True
            draft_final = _one_pass_rewrite(draft_final)
            # Re-eval once
            lines2 = _split_lines(draft_final)
            style_score2 = _style_score(lines2)
            # embed again
            line_vecs2 = []
            ok_embed = True
            for l in lines2:
                v = _embed_line(l)
                if not v:
                    ok_embed = False
                    break
                line_vecs2.append(v)
            if ok_embed and anchors:
                s_per_anchor2 = []
                for a in anchors:
                    av = _l2_normalize(a["vec"]) if isinstance(a.get("vec"), list) else None
                    if not av or len(av) != SIM_DIM:
                        continue
                    s_max2 = max(_cos(lv, av) for lv in line_vecs2)
                    s_per_anchor2.append(s_max2)
                s1_2 = (s_per_anchor2[0] if len(s_per_anchor2) > 0 else 0.0)
                s2_2 = (s_per_anchor2[1] if len(s_per_anchor2) > 1 else 0.0)
                score_line2 = max(s1_2, s2_2)
                # novelty again
                d_tri2 = _trigrams("\n".join(lines2))
                jaccs2 = []
                cos_ceiling_hit2 = False
                for a in anchors:
                    a_tri = _trigrams(a.get("text") or "")
                    jaccs2.append(_jaccard(a_tri, d_tri2))
                    av = _l2_normalize(a["vec"]) if isinstance(a.get("vec"), list) else None
                    if av:
                        for lv in line_vecs2:
                            if _cos(lv, av) > NOVELTY_COS_CEILING:
                                cos_ceiling_hit2 = True
                                break
                jaccard_max2 = max(jaccs2) if jaccs2 else 0.0
                # Update decisions
                style_score = style_score2
                score_line = score_line2
                jaccard_max = jaccard_max2
                cos_ceiling_hit = cos_ceiling_hit2
                blocked = (style_score < STYLE_MIN) or (score_line < UMBRAL_SIMILITUD_LINE) or (jaccard_max > NOVELTY_JACCARD_MAX) or cos_ceiling_hit
            else:
                blocked = True
        # Telemetry
        logger.info(
            "gate_telemetry pattern_selected=%s anchors=%s style_score=%.3f score_line=%.3f jaccard_max=%.3f near_dup=%s cos_ceiling_hit=%s after_rewrite=%s blocked=%s",
            pattern_selected,
            [a.get("id") for a in anchors],
            style_score,
            score_line,
            jaccard_max,
            near_dup,
            cos_ceiling_hit,
            after_rewrite,
            blocked,
        )
        return {
            "style_score": float(style_score),
            "score_line": float(score_line),
            "jaccard_max": float(jaccard_max),
            "near_dup": near_dup,
            "cos_ceiling_hit": bool(cos_ceiling_hit),
            "blocked": bool(blocked),
            "pattern_selected": pattern_selected,
            "anchors": [a.get("id") for a in anchors],
            "after_rewrite": bool(after_rewrite),
            "draft_final": draft_final,
        }, 200
    except Exception as exc:
        logger.error("/eval/full_gate failed: %s", exc, exc_info=True)
        return {"ok": False, "error": "server_error"}, 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
# Force re-deploy


def _tighten(text: str) -> str:
    # Quita conectores/adverbios comunes y relleno ligero
    patterns = [r"\bpero\b", r"\bsin\s+embargo\b", r"\bademás\b", r"\brealmente\b", r"\bclaramente\b", r"\bquizá\b", r"\bquizás\b", r"\bmuy\b"]
    out = text
    for p in patterns:
        out = re.sub(p, "", out, flags=re.IGNORECASE)
    out = re.sub(r"\s{2,}", " ", out)
    return out.strip()


def _reframe(text: str) -> str:
    # Sinónimos simples y reordenado ligero por comas/puntos
    out = text
    synonyms = [(r"\bproblema\b", "caos"), (r"\bmejora\b", "avance"), (r"\bempezar\b", "comenzar"), (r"\bcrear\b", "definir")]
    for src, tgt in synonyms:
        out = re.sub(src, tgt, out, flags=re.IGNORECASE)
    # Permuta el orden de oraciones si hay 3 o más
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", out) if p.strip()]
    if len(parts) >= 3:
        parts = [parts[1], parts[0], parts[2]] + parts[3:]
        out = " ".join(parts)
    return out


def _punch(text: str) -> str:
    # Refuerza imperativos y cierre
    lines = _split_lines(text)
    if not lines:
        lines = [text]
    verbs = ["define", "corrige", "aplica", "cierra", "prioriza", "avanza"]
    if lines:
        last = lines[-1].strip()
        if not last.endswith("!"):
            last = last + "!"
        lines[-1] = last
    return "\n".join(lines)


def _one_pass_rewrite(text: str) -> str:
    return _punch(_reframe(_tighten(text)))

# -------------------- Eval helpers --------------------

def _split_lines(text: str) -> list[str]:
    return [l.strip() for l in str(text).splitlines() if str(l).strip()]


def _l2_normalize(vec: list[float]) -> list[float]:
    if not vec:
        return []
    norm = math.sqrt(sum((x * x) for x in vec))
    if norm <= 1e-12:
        return vec[:]
    return [x / norm for x in vec]


def _cos(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    # Asumimos L2-normalizados; si no, el valor seguirá en [-1,1]
    return float(sum(x * y for x, y in zip(a, b)))


def _embed_line(text: str) -> Optional[list[float]]:
    try:
        # Política cache-only: no generar embeddings en rutas interactivas
        vec = get_embedding(text, generate_if_missing=False)
        if not isinstance(vec, list) or len(vec) != SIM_DIM:
            return None
        return _l2_normalize(vec)
    except Exception:
        return None


def _fetch_anchors_by_ids(ids: list[str]) -> list[Dict[str, Any]]:
    try:
        client = get_chroma_client()
        coll = client.get_or_create_collection(TOPICS_COLLECTION_NAME, metadata={"hnsw:space": "cosine"})
        res = coll.get(ids=ids, include=["documents", "embeddings", "metadatas", "ids"]) or {}
        out = []
        ids_r = res.get("ids") or []
        docs_r = res.get("documents") or []
        embs_r = res.get("embeddings") or []
        for id_, d, e in zip(ids_r, docs_r, embs_r):
            text = d[0] if isinstance(d, list) else d
            out.append({"id": id_, "text": text, "vec": e})
        return out
    except Exception:
        return []


def _trigrams(text: str) -> set[str]:
    toks = [t for t in re.findall(r"\w+", (text or "").lower()) if t]
    grams = set()
    for i in range(len(toks) - 2):
        grams.add(" ".join(toks[i : i + 3]))
    return grams


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = a.intersection(b)
    union = a.union(b)
    if not union:
        return 0.0
    return float(len(inter)) / float(len(union))


def _style_checks(lines: list[str]) -> float:
    # Heurística simple: penaliza relleno, exceso de longitud y recompensa cierre imperativo
    if not lines:
        return 0.0
    good = 0
    total = len(lines)
    bad_words = {"realmente", "claramente", "muy", "quizá", "quizás"}
    imperative_starts = {"define", "corrige", "aplica", "cierra", "prioriza", "avanza", "stop", "fix", "ship"}
    for ln in lines:
        s = ln.strip()
        if len(s) < 5 or len(s) > 180:
            continue
        if any(w in s.lower() for w in bad_words):
            continue
        reward = 0
        if s.endswith("!"):
            reward += 1
        first = s.split()[0].lower() if s.split() else ""
        if first in imperative_starts:
            reward += 1
        if reward >= 1:
            good += 1
    return good / total


def _style_score(lines: list[str]) -> float:
    # Normaliza a [0,1]
    return float(max(0.0, min(1.0, _style_checks(lines))))
