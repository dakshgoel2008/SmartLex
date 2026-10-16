import html
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from smartlex.core.logger import setup_logger

logger = setup_logger(__name__)

# Optional PyMuPDF import (falls back to system `pdftotext` if not installed)
try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None


def extract_text_pdf(file_path: str) -> str:
    """
    Extracts full text from a PDF file.
    Uses PyMuPDF (`fitz`) when available, or falls back to Poppler's `pdftotext`.
    """
    if fitz is not None:
        pages_text = []
        try:
            with fitz.open(file_path) as pdf_document:
                for page_num, page in enumerate(pdf_document, start=1):
                    page_str = page.get_text()
                    if page_str and page_str.strip():
                        pages_text.append(f"[Page {page_num}] {page_str}")
        except Exception as e:
            logger.error(f"Error reading PDF {file_path}: {e}")
        return "\n".join(pages_text)

    # Fallback: use `pdftotext` CLI if installed on the system
    pdftotext_bin = shutil.which("pdftotext")
    if pdftotext_bin:
        try:
            proc = subprocess.run(
                [pdftotext_bin, "-q", str(file_path), "-"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if proc.returncode == 0:
                return proc.stdout
        except Exception as e:
            logger.error(f"Error running pdftotext on {file_path}: {e}")

    return ""


def extract_text_docx(file_path: str) -> str:
    """
    Extracts 100% of text from a Word (.docx) file, including paragraphs,
    tables, headers, and footers, by reading the OpenXML ZIP archive directly.
    """
    chunks = []
    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            xml_targets = [
                name
                for name in zf.namelist()
                if name.startswith("word/")
                and name.endswith(".xml")
                and (
                    "document" in name
                    or "header" in name
                    or "footer" in name
                    or "footnotes" in name
                )
            ]
            for target in xml_targets:
                xml_bytes = zf.read(target)
                root = ET.fromstring(xml_bytes)
                # Extract every <w:t> text node in document order
                paragraphs = []
                for elem in root.iter():
                    tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                    if tag == "t" and elem.text:
                        paragraphs.append(elem.text)
                    elif tag in ("p", "tr", "br"):
                        paragraphs.append("\n")
                chunks.append("".join(paragraphs))
    except Exception as e:
        logger.error(f"Error reading DOCX {file_path}: {e}")
    return "\n".join(chunks)


def extract_text_plain(file_path: str, max_bytes: int = 10 * 1024 * 1024) -> str:
    """
    Extracts text from plain-text, Markdown, CSV, JSON, source code, and HTML files.
    """
    try:
        with open(file_path, "rb") as f:
            raw_bytes = f.read(max_bytes)
        text = raw_bytes.decode("utf-8", errors="ignore")
        if str(file_path).lower().endswith((".html", ".htm")):
            # Remove <script> and <style> blocks, then strip HTML tags
            text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r"<[^>]+>", " ", text)
            text = html.unescape(text)
        return text
    except Exception as e:
        logger.error(f"Error reading text file {file_path}: {e}")
        return ""


def extract_text_from_file(file_path: str) -> str:
    """
    Unified file text extractor supporting PDF, DOCX, and text/code formats.
    """
    ext = Path(file_path).suffix.lower()
    if ext == ".pdf":
        return extract_text_pdf(file_path)
    if ext == ".docx":
        return extract_text_docx(file_path)
    if ext in {
        ".txt",
        ".md",
        ".csv",
        ".json",
        ".py",
        ".cpp",
        ".c",
        ".h",
        ".hpp",
        ".js",
        ".ts",
        ".html",
        ".htm",
        ".xml",
        ".yaml",
        ".yml",
        ".ini",
        ".cfg",
        ".rst",
        ".log",
    }:
        return extract_text_plain(file_path)
    return ""
