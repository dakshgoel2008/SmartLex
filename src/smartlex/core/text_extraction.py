import html
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from smartlex.core.logger import setup_logger
from smartlex.core.media_extractors import (
    extract_text_audio,
    extract_text_epub,
    extract_text_image,
    extract_text_odf,
    extract_text_pptx,
    extract_text_video,
    extract_text_xlsx,
    parse_subtitle_text,
    run_image_ocr,
)

logger = setup_logger(__name__)

# Optional PyMuPDF import (falls back to system `pdftotext` if not installed)
try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None


def _ocr_scanned_pdf_pages(file_path: str, max_pages: int = 10) -> str:
    """
    If a PDF contains scanned images with zero digital text, renders pages to PNG
    via `pdftoppm` (if available) and runs OCR on each page.
    """
    pdftoppm_bin = shutil.which("pdftoppm")
    if not pdftoppm_bin:
        return ""

    ocr_pages = []
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            out_prefix = str(Path(tmpdir) / "page")
            subprocess.run(
                [
                    pdftoppm_bin,
                    "-png",
                    "-r",
                    "150",
                    "-l",
                    str(max_pages),
                    str(file_path),
                    out_prefix,
                ],
                capture_output=True,
                timeout=30,
                check=False,
            )
            rendered = sorted(Path(tmpdir).glob("page*.png"))
            for page_idx, img_path in enumerate(rendered, start=1):
                page_ocr = run_image_ocr(str(img_path))
                if page_ocr:
                    ocr_pages.append(f"[Page {page_idx}] {page_ocr}")
    except Exception as e:
        logger.debug(f"Scanned PDF OCR fallback error on {file_path}: {e}")

    return "\n".join(ocr_pages)


def extract_text_pdf(file_path: str) -> str:
    """
    Extracts full text from a PDF file with `[Page N]` markers.
    Uses PyMuPDF (`fitz`) when available, or falls back to Poppler's `pdftotext`.
    Automatically falls back to OCR for scanned image-only PDFs when an OCR backend is active.
    """
    if fitz is not None:
        pages_text = []
        try:
            with fitz.open(file_path) as pdf_document:
                for page_num, page in enumerate(pdf_document, start=1):
                    page_str = page.get_text()
                    if page_str and page_str.strip():
                        pages_text.append(f"[Page {page_num}] {page_str.strip()}")
        except Exception as e:
            logger.error(f"Error reading PDF {file_path}: {e}")
        result = "\n".join(pages_text)
        if result.strip():
            return result

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
            if proc.returncode == 0 and proc.stdout.strip():
                # Split form-feed (\x0c) pages so `[Page N]` markers are preserved
                raw_pages = proc.stdout.split("\x0c")
                pages_out = []
                for idx, p_txt in enumerate(raw_pages, start=1):
                    if p_txt.strip():
                        pages_out.append(f"[Page {idx}] {p_txt.strip()}")
                if pages_out:
                    return "\n".join(pages_out)
        except Exception as e:
            logger.error(f"Error running pdftotext on {file_path}: {e}")

    # Scanned PDF fallback (if digital text extraction yielded nothing)
    return _ocr_scanned_pdf_pages(file_path)


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
            text = re.sub(
                r"<(script|style)[^>]*>.*?</\1>",
                " ",
                text,
                flags=re.DOTALL | re.IGNORECASE,
            )
            text = re.sub(r"<[^>]+>", " ", text)
            text = html.unescape(text)
        return text
    except Exception as e:
        logger.error(f"Error reading text file {file_path}: {e}")
        return ""


IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".bmp",
    ".tiff",
    ".tif",
    ".gif",
}

AUDIO_EXTENSIONS = {
    ".mp3",
    ".wav",
    ".flac",
    ".m4a",
    ".ogg",
    ".aac",
    ".opus",
}

VIDEO_EXTENSIONS = {
    ".mp4",
    ".mkv",
    ".mov",
    ".webm",
    ".avi",
    ".m4v",
}

SUBTITLE_EXTENSIONS = {
    ".srt",
    ".vtt",
    ".lrc",
}

PLAIN_TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".csv",
    ".tsv",
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
    ".css",
    ".xml",
    ".yaml",
    ".yml",
    ".ini",
    ".cfg",
    ".rst",
    ".log",
    ".sh",
    ".rs",
    ".go",
    ".java",
}


def extract_text_from_file(file_path: str) -> str:
    """
    Unified multimodal file text extractor supporting:
      - PDFs (.pdf with [Page N] markers + scanned PDF OCR fallback)
      - Office Archives (.docx, .xlsx with [Sheet: Name], .pptx with [Slide N], .odt/.ods/.odp)
      - E-Books (.epub with [Chapter N] markers)
      - Images & Screenshots (.png, .jpg, .webp, .bmp, .tiff, .gif -> EXIF/PNG tags + OCR)
      - Audio (.mp3, .wav, .flac, .m4a, .ogg -> ID3/RIFF/Vorbis/MP4 tags + transcripts/ASR)
      - Video (.mp4, .mkv, .mov, .webm, .avi -> Container metadata + [MM:SS] subtitles/ASR)
      - Subtitles/Lyrics (.srt, .vtt, .lrc -> [MM:SS] timestamped text)
      - Plain Text & Source Code (.txt, .md, .csv, .json, .py, .cpp, .html, etc.)
    """
    ext = Path(file_path).suffix.lower()
    if ext == ".pdf":
        return extract_text_pdf(file_path)
    if ext == ".docx":
        return extract_text_docx(file_path)
    if ext == ".xlsx":
        return extract_text_xlsx(file_path)
    if ext == ".pptx":
        return extract_text_pptx(file_path)
    if ext == ".epub":
        return extract_text_epub(file_path)
    if ext in {".odt", ".ods", ".odp"}:
        return extract_text_odf(file_path)
    if ext in IMAGE_EXTENSIONS:
        return extract_text_image(file_path)
    if ext in AUDIO_EXTENSIONS:
        return extract_text_audio(file_path)
    if ext in VIDEO_EXTENSIONS:
        return extract_text_video(file_path)
    if ext in SUBTITLE_EXTENSIONS:
        try:
            raw = Path(file_path).read_text(encoding="utf-8", errors="ignore")
            return parse_subtitle_text(raw)
        except OSError:
            return ""
    if ext in PLAIN_TEXT_EXTENSIONS:
        return extract_text_plain(file_path)
    return ""
