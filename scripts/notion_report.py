"""CLI util para avisar cuántos temas están en revisión en Notion."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from logger_config import logger
from notifications import send_telegram_message
from notion_ops import count_pages_by_status


def main():  # pragma: no cover - CLI entry point
    parser = argparse.ArgumentParser(description="Notifica cuántos temas están en Review en Notion.")
    parser.add_argument("--token", help="Token de Notion (default NOTION_API_TOKEN).")
    parser.add_argument("--database", help="Database ID (default NOTION_DATABASE_ID).")
    parser.add_argument("--status", default="Review", help="Estado a contar (default Review).")
    parser.add_argument(
        "--threshold",
        type=int,
        default=1,
        help="Número mínimo de temas para enviar notificación (default 1).",
    )
    args = parser.parse_args()

    token = args.token or os.getenv("NOTION_API_TOKEN")
    database_id = args.database or os.getenv("NOTION_DATABASE_ID")
    if not token or not database_id:
        raise SystemExit("Configura NOTION_API_TOKEN y NOTION_DATABASE_ID o pasa --token/--database.")

    count = count_pages_by_status(token, database_id, args.status)
    logger.info("Temas en estado %s: %s", args.status, count)

    if count >= args.threshold:
        message = (
            f"🚨 Tienes {count} temas en estado {args.status} en Notion. "
            "Dales el sello antes de la próxima promoción."
        )
        send_telegram_message(message)


if __name__ == "__main__":  # pragma: no cover
    main()
