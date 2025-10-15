# core/text_extraction.py
import fitz
from docx import Document

from core.logger import setup_logger

logger = setup_logger(__name__)


def extract_text_pdf(file_path):
    text = ""
    try:
        with fitz.open(file_path) as pdf_document:
            for page in pdf_document:
                text += page.get_text()
    except Exception as e:
        logger.error(f"Error reading PDF {file_path}: {e}")
    return text


def extract_text_docx(file_path):
    text = ""
    try:
        doc = Document(file_path)
        text = "\n".join([p.text for p in doc.paragraphs])
    except Exception as e:
        logger.error(f"Error reading DOCX {file_path}: {e}")
    return text
