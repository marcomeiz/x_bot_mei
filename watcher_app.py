import asyncio
import os
import time
from typing import Optional

from desktop_notifier import DesktopNotifier
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from ingestion_config import WatcherConfig, ensure_directories, load_config
from logger_config import logger
from pdf_extractor import extract_text_from_pdf
from persistence_service import PersistenceSummary, persist_topics, write_summary_json
from prompt_context import build_prompt_context
from topic_pipeline import TopicRecord, collect_valid_topics, configure_llama_index, extract_topics


class TopicIngestionService:
    def __init__(self, cfg: WatcherConfig, notifier: Optional[DesktopNotifier] = None) -> None:
        self.cfg = cfg
        self.notifier = notifier or DesktopNotifier()

    async def handle_new_pdf(self, pdf_path: str) -> None:
        pdf_name = os.path.splitext(os.path.basename(pdf_path))[0]
        logger.info("Nuevo PDF detectado: %s", pdf_name)

        await self._wait_for_copy(pdf_path)

        txt_path = os.path.join(self.cfg.text_dir, f"{pdf_name}.txt")
        text = extract_text_from_pdf(pdf_path, txt_path)

        context = build_prompt_context()
        raw_topics = extract_topics(text, context)
        records = collect_valid_topics(raw_topics, pdf_name, self.cfg, context)

        summary = persist_topics(records, self.cfg)
        output_path = write_summary_json(pdf_name, records, self.cfg.json_dir)

        logger.info(
            "Proceso completado para '%s' | Temas extraídos=%s, aprobados=%s",
            pdf_name,
            len(raw_topics),
            len(records),
        )
        logger.info("Resumen guardado en %s", output_path)

        if summary.added > 0:
            await self.notifier.send(
                title=f"✅ Proceso completado: {pdf_name}",
                message=f"Se han añadido {summary.added} nuevos temas a la base de datos.",
            )

    async def _wait_for_copy(self, path: str) -> None:
        prev_size = -1
        while True:
            current_size = os.path.getsize(path)
            if current_size == prev_size:
                break
            prev_size = current_size
            await asyncio.sleep(1)


class PDFHandler(FileSystemEventHandler):
    def __init__(self, service: TopicIngestionService) -> None:
        super().__init__()
        self.service = service

    def on_created(self, event):  # pragma: no cover - filesystem callback
        if event.is_directory or not event.src_path.lower().endswith(".pdf"):
            return

        async def _process():
            try:
                await self.service.handle_new_pdf(event.src_path)
            except Exception as exc:
                logger.error("Fallo procesando %s: %s", event.src_path, exc, exc_info=True)

        asyncio.run(_process())


def main():  # pragma: no cover - CLI entry point
    cfg = load_config()
    ensure_directories((cfg.upload_dir, cfg.text_dir, cfg.json_dir))
    configure_llama_index()

    service = TopicIngestionService(cfg)
    handler = PDFHandler(service)

    observer = Observer()
    observer.schedule(handler, cfg.upload_dir, recursive=False)
    observer.start()
    logger.info("Vigilando la carpeta %s para nuevos PDFs…", cfg.upload_dir)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


if __name__ == "__main__":  # pragma: no cover
    main()

