print("---- ESTA ES LA PUTA VERSIÓN NUEVA DEL CÓDIGO: v_FINAL ----", flush=True)

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from queue import Full, Queue
from typing import Any, Callable, Dict, Optional

from dotenv import load_dotenv
from flask import Flask, request

from admin_service import AdminService
from draft_repository import DraftRepository
from logger_config import logger
from proposal_service import ProposalService
from src.messages import get_message
from telegram_client import TelegramClient

# Warmup/health imports
from embeddings_manager import get_chroma_client, get_topics_collection, get_embedding

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SHOW_TOPIC_ID = os.getenv("SHOW_TOPIC_ID", "0").lower() in ("1", "true", "yes", "y")
ADMIN_API_TOKEN = os.getenv("ADMIN_API_TOKEN", "")
TELEGRAM_SECRET_TOKEN = os.getenv("TELEGRAM_SECRET_TOKEN", "")
SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", "0.20") or 0.20)
TEMP_DIR = os.getenv("BOT_TEMP_DIR", "/tmp")
JOB_MAX_WORKERS = int(os.getenv("JOB_MAX_WORKERS", "3") or 3)
JOB_QUEUE_MAXSIZE = int(os.getenv("JOB_QUEUE_MAXSIZE", "12") or 12)
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
            # Fallback: si aún faltan, generar embeddings on the fly
            if added < need:
                remaining = need - added
                for d in t_docs[:remaining]:
                    text = d[0] if isinstance(d, list) else d
                    vec = get_embedding(text)
                    if vec:
                        _ANCHORS_CACHE.append({"text": text, "vec": vec})
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
    chat_id = job["chat_id"]
    func: Callable[..., None] = job["func"]
    args = job.get("args", ())
    kwargs = job.get("kwargs", {})
    try:
        func(*args, **kwargs)
    except Exception as exc:
        logger.error("[CHAT_ID: %s] Error ejecutando job: %s", chat_id, exc, exc_info=True)
        try:
            telegram_client.send_message(chat_id, get_message("generate_error"))
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
        telegram_client.send_message(chat_id, get_message("queue_busy"))
        return
    job = {
        "chat_id": chat_id,
        "func": proposal_service.do_the_work,
        "args": (chat_id,),
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
        telegram_client.send_message(chat_id, get_message("queue_full"))


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


@app.route("/health/embeddings")
def health_embeddings():
    """Devuelve dims y warmup; ok=true si ambos dims==SIM_DIM y warmed>=WARMUP_ANCHORS."""
    try:
        client = get_chroma_client()
        gold = client.get_or_create_collection("goldset_collection", metadata={"hnsw:space": "cosine"})
        g_res = gold.get(include=["embeddings"]) or {}
        g_vecs = g_res.get("embeddings") or []
        gold_dim = (len(g_vecs[0]) if g_vecs else None)

        topics = client.get_or_create_collection(TOPICS_COLLECTION_NAME, metadata={"hnsw:space": "cosine"})
        t_res = topics.get(include=["embeddings"]) or {}
        t_vecs = t_res.get("embeddings") or []
        topics_dim = (len(t_vecs[0]) if t_vecs else None)

        warmed = len(_ANCHORS_CACHE)
        ok = (gold_dim == SIM_DIM) and (topics_dim == SIM_DIM) and (warmed >= WARMUP_ANCHORS)
        return {
            "goldset_dim": gold_dim,
            "topics_dim": topics_dim,
            "warmed": warmed,
            "ok": ok,
        }, 200
    except Exception:
        return {"ok": False, "error": "server_error"}, 500


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
