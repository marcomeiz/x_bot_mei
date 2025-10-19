"""Ejecuta la promoción y envía una notificación si hubo novedades."""

from __future__ import annotations

import argparse
import os

from logger_config import logger
from notifications import send_telegram_message
from promote_notion_topics import PromotionSummary, promote_validated_topics


def _notify(summary: PromotionSummary, status: str) -> None:
    if summary.added <= 0:
        return
    message = (
        f"✅ {summary.added} temas aprobados y enviados a la memoria. "
        f"(status origen: {status}; procesados={summary.processed}, saltados={summary.skipped}, errores={summary.errored})"
    )
    send_telegram_message(message)


def main():  # pragma: no cover - CLI entry point
    parser = argparse.ArgumentParser(description="Promueve temas validados y avisa por Telegram.")
    parser.add_argument("--token", help="Token de Notion (default NOTION_API_TOKEN).")
    parser.add_argument("--database", help="Database ID (default NOTION_DATABASE_ID).")
    parser.add_argument("--status", default="Validated", help="Estado a promover (default Validated).")
    parser.add_argument("--set-status", default="Promoted", help="Nuevo estado en Notion tras promover.")
    parser.add_argument("--synced-property", default="Synced", help="Checkbox a marcar como sincronizado.")
    parser.add_argument("--dry-run", action="store_true", help="Solo simula la promoción.")
    args = parser.parse_args()

    token = args.token or os.getenv("NOTION_API_TOKEN")
    database_id = args.database or os.getenv("NOTION_DATABASE_ID")
    if not token or not database_id:
        raise SystemExit("Configura NOTION_API_TOKEN y NOTION_DATABASE_ID o pasa --token/--database.")

    summary = promote_validated_topics(
        token=token,
        database_id=database_id,
        status=args.status,
        set_status=args.set_status,
        sync_checkbox=args.synced_property,
        dry_run=args.dry_run,
    )

    logger.info(
        "Promoción finalizada. Procesados=%s añadidos=%s saltados=%s errores=%s",
        summary.processed,
        summary.added,
        summary.skipped,
        summary.errored,
    )

    if not args.dry_run:
        _notify(summary, args.status)


if __name__ == "__main__":  # pragma: no cover
    main()
