"""CLI para ingerir señales desde Hugging Face y generar candidatos de temas."""

import argparse
import json

from huggingface_ingestion.ingestion import run_ingestion
from logger_config import logger
from notifications import send_telegram_message


def main():
    parser = argparse.ArgumentParser(description="Ingesta de datasets de Hugging Face.")
    parser.add_argument(
        "--config",
        help="Ruta al archivo de configuración de fuentes (por defecto config/hf_sources.json).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Límite duro de ejemplos totales a procesar (todas las fuentes combinadas).",
    )
    parser.add_argument(
        "--notify",
        action="store_true",
        help="Envía una notificación a Telegram si se crean candidatos nuevos.",
    )
    args = parser.parse_args()

    summary = run_ingestion(config_path=args.config, limit=args.limit)
    logger.info("Resumen de ingestión HF: %s", json.dumps(summary, ensure_ascii=False, indent=2))

    if args.notify and summary.get("accepted"):
        sources = ", ".join(sorted(summary.get("sources", {}).keys())) or "fuentes desconocidas"
        send_telegram_message(
            f"📥 Se generaron {summary['accepted']} nuevos candidatos desde {sources}."
        )


if __name__ == "__main__":  # pragma: no cover
    main()
