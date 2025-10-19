import fitz  # PyMuPDF

from logger_config import logger


def extract_text_from_pdf(pdf_path: str, output_txt_path: str) -> str:
    """Read a PDF, concatenate its pages and write a plain-text artifact."""
    with fitz.open(pdf_path) as doc:
        text = "\n".join(page.get_text() for page in doc)
    with open(output_txt_path, "w", encoding="utf-8") as handle:
        handle.write(text)
    logger.info("Texto extraído -> %s", output_txt_path)
    return text

