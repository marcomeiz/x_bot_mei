"""CLI para ingerir señales desde Hugging Face y generar candidatos de temas."""

import argparse
import json

from huggingface_ingestion.ingestion import run_ingestion
from logger_config import logger


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
    args = parser.parse_args()

    summary = run_ingestion(config_path=args.config, limit=args.limit)
    logger.info("Resumen de ingestión HF: %s", json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":  # pragma: no cover
    main()
