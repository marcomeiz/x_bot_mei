import html
import re
from typing import Dict, Optional

import requests

from logger_config import logger


class TelegramClient:
    """Wraps Telegram HTTP interactions and message formatting."""

    def __init__(self, bot_token: str, show_topic_id: bool = False) -> None:
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self.show_topic_id = show_topic_id

    # Keyboards ----------------------------------------------------------------
    @staticmethod
    def get_new_tweet_keyboard() -> dict:
        return {"inline_keyboard": [[{"text": "🚀 Generar Nuevo Tuit", "callback_data": "generate_new"}]]}

    # Formatting ---------------------------------------------------------------
    @staticmethod
    def clean_abstract(text: str, max_len: int = 160) -> str:
        if not isinstance(text, str):
            return ""
        t = re.sub(r"#\S+", "", text)
        t = re.sub(r"\s+", " ", t).strip()
        if max_len and len(t) > max_len:
            cut = t[:max_len].rstrip()
            if " " in cut:
                cut = cut[: cut.rfind(" ")].rstrip()
            t = cut + "…"
        return t

    @staticmethod
    def escape(text: Optional[str]) -> str:
        if text is None:
            return ""
        return html.escape(text)

    def format_proposal_message(
        self,
        topic_id: str,
        abstract: str,
        source_pdf: Optional[str],
        draft_a: str,
        draft_b: str,
        draft_c: Optional[str] = None,
        category_name: Optional[str] = None,
        evaluations: Optional[Dict[str, Dict[str, object]]] = None,
    ) -> str:
        len_a = len(draft_a or "")
        len_b = len(draft_b or "")
        len_c = len(draft_c or "") if draft_c else 0

        safe_id = self.escape(topic_id or "-")
        safe_abstract = self.escape(self.clean_abstract(abstract or ""))
        safe_source = self.escape(source_pdf) if source_pdf else None
        safe_a = self.escape(draft_a or "")
        safe_b = self.escape(draft_b or "")
        safe_category = self.escape(category_name) if category_name else None

        lines: list[str] = ["<b>Borradores</b>"]
        if self.show_topic_id or not source_pdf:
            lines.append(f"<b>ID:</b> {safe_id}")
        if safe_abstract:
            lines.append(f"<b>Tema:</b> {safe_abstract}")
        if safe_source:
            lines.append(f"<b>Origen:</b> {safe_source}")
        if draft_c and safe_category:
            lines.append(f"<b>Categoría (C):</b> {safe_category}")

        lines.append("")
        lines.append(f"<b>A · {len_a}/280</b>\n{safe_a}")
        lines.append("")
        lines.append(f"<b>B · {len_b}/280</b>\n{safe_b}")
        if draft_c:
            safe_c = self.escape(draft_c or "")
            lines.append("")
            lines.append(f"<b>C · {len_c}/280</b>\n{safe_c}")

        if evaluations:
            lines.extend(self._build_evaluation_lines(evaluations))

        return "\n".join(lines).strip()

    def _build_evaluation_lines(self, evaluations: Dict[str, Dict[str, object]]) -> list[str]:
        lines = ["", "<b>Evaluación automática</b>"]
        for label, data in evaluations.items():
            style = data.get("style_score")
            factuality = data.get("factuality")
            needs_revision = bool(data.get("needs_revision"))
            summary = str(data.get("summary", "")).strip()
            parts = []
            if style is not None:
                parts.append(f"⭐{style}/5")
            if factuality:
                parts.append(f"Factualidad: {str(factuality).upper()}")
            if summary:
                parts.append(summary)
            line = " | ".join(parts) if parts else "Sin datos"
            prefix = "⚠️ " if needs_revision else ""
            lines.append(f"{label}: {self.escape(prefix + line)}")
        return lines

    def build_proposal_keyboard(self, topic_id: str, has_variant_c: bool, allow_variant_c: bool) -> Dict:
        rows = [
            [
                {"text": "👍 Aprobar A", "callback_data": f"approve_A_{topic_id}"},
                {"text": "👍 Aprobar B", "callback_data": f"approve_B_{topic_id}"},
            ],
            [
                {"text": "📋 Copiar A", "callback_data": f"copy_A_{topic_id}"},
                {"text": "📋 Copiar B", "callback_data": f"copy_B_{topic_id}"},
            ],
        ]

        if has_variant_c:
            if allow_variant_c:
                rows.append(
                    [{"text": "👍 Aprobar C", "callback_data": f"approve_C_{topic_id}"}]
                )
                rows.append(
                    [{"text": "📋 Copiar C", "callback_data": f"copy_C_{topic_id}"}]
                )
            else:
                rows.append(
                    [{"text": "⚠️ C Rechazada", "callback_data": "noop"}]
                )

        rows.append([{"text": "👎 Rechazar Todos", "callback_data": f"reject_{topic_id}"}])
        rows.append([{"text": "🔁 Generar Nuevo", "callback_data": "generate_new"}])
        return {"inline_keyboard": rows}

    # HTTP ---------------------------------------------------------------------
    def _post(self, endpoint: str, payload: dict, chat_id: int) -> bool:
        url = f"{self.base_url}/{endpoint}"
        try:
            response = requests.post(url, json=payload, timeout=20)
            data = {}
            try:
                data = response.json()
            except Exception:
                pass
            if response.status_code != 200 or not data.get("ok", True):
                logger.error(
                    "[CHAT_ID: %s] Telegram API error: status=%s, resp=%s",
                    chat_id,
                    response.status_code,
                    data,
                )
                return False
            return True
        except Exception as exc:
            logger.error("[CHAT_ID: %s] Telegram HTTP error: %s", chat_id, exc, exc_info=True)
            return False

    def send_message(self, chat_id: int, text: str, reply_markup=None, as_html: bool = False) -> bool:
        safe_text = text if as_html else self.escape(text)
        payload = {"chat_id": chat_id, "text": safe_text, "parse_mode": "HTML"}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        if self._post("sendMessage", payload, chat_id):
            return True

        # fallback plain text
        payload = {"chat_id": chat_id, "text": text}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return self._post("sendMessage", payload, chat_id)

    def edit_message(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup=None,
        as_html: bool = False,
    ) -> bool:
        safe_text = text if as_html else self.escape(text)
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": safe_text,
            "parse_mode": "HTML",
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        if self._post("editMessageText", payload, chat_id):
            return True

        payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return self._post("editMessageText", payload, chat_id)
