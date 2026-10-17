"""Text and metadata extractors for office files, e-books, images, audio and video."""

import html
import importlib.metadata
import importlib.util
import math
import os
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import wave
import xml.etree.ElementTree as ET
import zipfile
from functools import lru_cache
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from smartlex.core.logger import setup_logger

logger = setup_logger(__name__)

# Optional Pillow import for image metadata & OCR preprocessing
try:
    from PIL import ExifTags, Image, ImageOps, ImageStat
except ImportError:
    Image = None
    ExifTags = None
    ImageOps = None
    ImageStat = None

# Pluggable callbacks for OCR (image_path -> str) and ASR (media_path -> str)
# Allows unit tests, custom local models, or enterprise plugins to hook in cleanly.
_custom_ocr_backend: Optional[Callable[[str], str]] = None
_custom_asr_backend: Optional[Callable[[str], str]] = None


def register_ocr_backend(fn: Optional[Callable[[str], str]]) -> None:
    """Registers a custom OCR function `fn(image_path: str) -> str`."""
    global _custom_ocr_backend
    _custom_ocr_backend = fn


def register_asr_backend(fn: Optional[Callable[[str], str]]) -> None:
    """Registers a custom Speech-to-Text ASR function `fn(media_path: str) -> str`."""
    global _custom_asr_backend
    _custom_asr_backend = fn


def format_timestamp(seconds: float) -> str:
    """Formats seconds into `[MM:SS]` or `[HH:MM:SS]` for searchable media snippets."""
    total_secs = max(0, int(round(seconds)))
    hours = total_secs // 3600
    minutes = (total_secs % 3600) // 60
    secs = total_secs % 60
    if hours > 0:
        return f"[{hours:02d}:{minutes:02d}:{secs:02d}]"
    return f"[{minutes:02d}:{secs:02d}]"


def extract_text_xlsx(file_path: str) -> str:
    """Extracts cell text from an .xlsx workbook, prefixing each sheet with `[Sheet: Name]`."""
    output_sections: List[str] = []
    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            namelist = set(zf.namelist())

            # 1. Load Shared Strings Table (`xl/sharedStrings.xml`)
            shared_strings: List[str] = []
            if "xl/sharedStrings.xml" in namelist:
                ss_root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
                for si in ss_root.iter():
                    tag = si.tag.split("}")[-1] if "}" in si.tag else si.tag
                    if tag == "si":
                        parts = []
                        for child in si.iter():
                            ctag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                            if ctag == "t" and child.text:
                                parts.append(child.text)
                        shared_strings.append("".join(parts))

            # 2. Map sheet filenames (`xl/worksheets/sheet1.xml`) to human-readable tab names
            sheet_names: Dict[str, str] = {}
            if "xl/workbook.xml" in namelist:
                wb_root = ET.fromstring(zf.read("xl/workbook.xml"))
                sheet_idx = 1
                for elem in wb_root.iter():
                    tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                    if tag == "sheet":
                        name = elem.attrib.get("name", f"Sheet{sheet_idx}")
                        sheet_names[f"xl/worksheets/sheet{sheet_idx}.xml"] = name
                        sheet_idx += 1

            # 3. Parse every worksheet in numerical order
            worksheet_files = sorted(
                [
                    f
                    for f in namelist
                    if f.startswith("xl/worksheets/sheet") and f.endswith(".xml")
                ],
                key=lambda x: [int(s) if s.isdigit() else s for s in re.split(r"(\d+)", x)],
            )

            for ws_file in worksheet_files:
                tab_name = sheet_names.get(ws_file, Path(ws_file).stem)
                ws_root = ET.fromstring(zf.read(ws_file))
                rows_text: List[str] = []

                for elem in ws_root.iter():
                    tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                    if tag == "row":
                        row_cells: List[str] = []
                        for cell in elem:
                            ctag = cell.tag.split("}")[-1] if "}" in cell.tag else cell.tag
                            if ctag != "c":
                                continue
                            cell_type = cell.attrib.get("t", "")
                            val_text = ""

                            if cell_type == "inlineStr":
                                parts = [
                                    n.text
                                    for n in cell.iter()
                                    if (n.tag.split("}")[-1] if "}" in n.tag else n.tag) == "t"
                                    and n.text
                                ]
                                val_text = "".join(parts)
                            else:
                                for sub in cell:
                                    stag = sub.tag.split("}")[-1] if "}" in sub.tag else sub.tag
                                    if stag == "v" and sub.text is not None:
                                        raw_v = sub.text.strip()
                                        if cell_type == "s" and raw_v.isdigit():
                                            idx = int(raw_v)
                                            if 0 <= idx < len(shared_strings):
                                                val_text = shared_strings[idx]
                                        else:
                                            val_text = raw_v
                                        break

                            if val_text.strip():
                                row_cells.append(val_text.strip())

                        if row_cells:
                            rows_text.append(" | ".join(row_cells))

                if rows_text:
                    output_sections.append(f"[Sheet: {tab_name}] " + "\n".join(rows_text))

    except Exception as e:
        logger.error(f"Error extracting XLSX {file_path}: {e}")

    return "\n\n".join(output_sections)


def extract_text_pptx(file_path: str) -> str:
    """
    Extracts slide text and speaker notes from a PowerPoint (.pptx) file.
    Prefixes each slide with `[Slide N]` so search snippets show the exact slide.
    """
    slide_sections: List[str] = []
    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            namelist = set(zf.namelist())

            slide_files = sorted(
                [
                    f
                    for f in namelist
                    if re.match(r"^ppt/slides/slide\d+\.xml$", f)
                ],
                key=lambda x: int(re.search(r"(\d+)", x).group(1)),
            )

            for slide_file in slide_files:
                slide_num = int(re.search(r"(\d+)", slide_file).group(1))
                root = ET.fromstring(zf.read(slide_file))

                texts: List[str] = []
                for elem in root.iter():
                    tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                    if tag == "t" and elem.text and elem.text.strip():
                        texts.append(elem.text.strip())

                # Also check speaker notes for this slide (`ppt/notesSlides/notesSlideN.xml`)
                notes_file = f"ppt/notesSlides/notesSlide{slide_num}.xml"
                if notes_file in namelist:
                    notes_root = ET.fromstring(zf.read(notes_file))
                    for elem in notes_root.iter():
                        tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                        if tag == "t" and elem.text and elem.text.strip():
                            val = elem.text.strip()
                            # Ignore standalone slide number placeholders
                            if val != str(slide_num):
                                texts.append(val)

                if texts:
                    slide_sections.append(f"[Slide {slide_num}] " + " ".join(texts))

    except Exception as e:
        logger.error(f"Error extracting PPTX {file_path}: {e}")

    return "\n".join(slide_sections)


def _strip_html_to_text(raw_html: str) -> str:
    """Helper to strip scripts, styles, and HTML/XHTML tags cleanly."""
    cleaned = re.sub(
        r"<(script|style)[^>]*>.*?</\1>",
        " ",
        raw_html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = html.unescape(cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def extract_text_epub(file_path: str) -> str:
    """
    Extracts book metadata (title, author, description) and full chapter text
    from an EPUB (.epub) e-book archive.
    """
    sections: List[str] = []
    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            namelist = zf.namelist()

            # 1. Extract OPF metadata (dc:title, dc:creator, dc:description)
            opf_files = [f for f in namelist if f.lower().endswith(".opf")]
            spine_hrefs: List[str] = []
            opf_dir = ""

            if opf_files:
                opf_path = opf_files[0]
                opf_dir = str(Path(opf_path).parent)
                if opf_dir == ".":
                    opf_dir = ""
                opf_root = ET.fromstring(zf.read(opf_path))

                meta_parts: List[str] = []
                manifest_map: Dict[str, str] = {}

                for elem in opf_root.iter():
                    tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                    if tag in ("title", "creator", "description", "subject") and elem.text:
                        meta_parts.append(elem.text.strip())
                    elif tag == "item":
                        item_id = elem.attrib.get("id", "")
                        href = elem.attrib.get("href", "")
                        if item_id and href:
                            manifest_map[item_id] = href
                    elif tag == "itemref":
                        idref = elem.attrib.get("idref", "")
                        if idref in manifest_map:
                            spine_hrefs.append(manifest_map[idref])

                if meta_parts:
                    sections.append(" — ".join(meta_parts))

            # 2. Read HTML/XHTML chapters in spine order (or fallback to archive order)
            chapter_files: List[str] = []
            namelist_set = set(namelist)
            for href in spine_hrefs:
                full_href = f"{opf_dir}/{href}".lstrip("/") if opf_dir else href
                if full_href in namelist_set:
                    chapter_files.append(full_href)
                elif href in namelist_set:
                    chapter_files.append(href)

            if not chapter_files:
                chapter_files = [
                    f
                    for f in namelist
                    if f.lower().endswith((".xhtml", ".html", ".htm"))
                    and "nav" not in f.lower()
                ]

            for ch_idx, ch_file in enumerate(chapter_files, start=1):
                raw_bytes = zf.read(ch_file)
                ch_text = _strip_html_to_text(raw_bytes.decode("utf-8", errors="ignore"))
                if ch_text:
                    sections.append(f"[Chapter {ch_idx}] {ch_text}")

    except Exception as e:
        logger.error(f"Error extracting EPUB {file_path}: {e}")

    return "\n\n".join(sections)


def extract_text_odf(file_path: str) -> str:
    """
    Extracts text from OpenDocument files (.odt, .ods, .odp) via `content.xml`.
    """
    parts: List[str] = []
    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            for target in ("meta.xml", "content.xml"):
                if target in zf.namelist():
                    root = ET.fromstring(zf.read(target))
                    for elem in root.iter():
                        if elem.text and elem.text.strip():
                            parts.append(elem.text.strip())
                        if elem.tail and elem.tail.strip():
                            parts.append(elem.tail.strip())
    except Exception as e:
        logger.error(f"Error extracting ODF {file_path}: {e}")
    return " ".join(parts)


# Image metadata is instant to read; OCR takes seconds per image, so the indexer
# runs it as a separate background pass (see processor.run_ocr_pass).
MIN_OCR_SIDE = 8                # images smaller than this cannot hold readable text
MAX_OCR_PIXELS = 80_000_000     # larger images are skipped to bound memory and time
MIN_OCR_CONTRAST = 12           # a near-uniform image (blank page, solid colour) has no text
MAX_OCR_PAGES = 10              # pages read from a multi-page TIFF
OCR_MAX_SIDE = 2000             # the OCR engine shrinks images whose long side exceeds this
TILE_ASPECT_RATIO = 2.5         # long, narrow images above OCR_MAX_SIDE are read in tiles
MAX_OCR_TILES = 12
SIDECAR_MAX_BYTES = 1_000_000

_rapidocr_engine = None
_rapidocr_failed = False
_rapidocr_lock = threading.Lock()


def _decode_exif_value(val) -> str:
    """Decodes EXIF byte sequences (including UTF-16LE Windows XP tags & UserComment)."""
    if isinstance(val, bytes):
        if val.startswith(b"ASCII\x00\x00\x00"):
            val = val[8:]
        elif val.startswith(b"UNICODE\x00"):
            return val[8:].decode("utf-16-le", errors="ignore").replace("\x00", "").strip()
        # Check if it looks like UTF-16LE (alternating null bytes)
        if len(val) >= 4 and val[1] == 0 and val[3] == 0:
            return val.decode("utf-16-le", errors="ignore").replace("\x00", "").strip()
        return val.decode("utf-8", errors="ignore").replace("\x00", "").strip()
    if isinstance(val, (tuple, list)):
        # Windows XP tags sometimes come from Pillow as tuple of byte ints
        if val and all(isinstance(x, int) and 0 <= x <= 255 for x in val):
            return bytes(val).decode("utf-16-le", errors="ignore").replace("\x00", "").strip()
        return " ".join(str(x) for x in val)
    return str(val).replace("\x00", "").strip()


# Only the values are indexed, never the field names: indexing "Software:" or
# "Title:" would make every image match a search for "software" or "title".
_IMAGE_INFO_KEYS = {
    "title", "description", "comment", "comments", "author", "keywords", "subject",
    "software", "copyright", "source", "parameters", "prompt", "ocr_text",
}

_EXIF_TEXT_TAGS = {
    "ImageDescription", "Make", "Model", "Software", "Artist", "Copyright",
    "UserComment", "XPTitle", "XPComment", "XPAuthor", "XPKeywords", "XPSubject",
    "DocumentName",
}

_EXIF_DATE_TAGS = {"DateTime", "DateTimeOriginal"}


def _embedded_text_values(img) -> List[str]:
    """Text chunks stored in PNG, WebP and GIF files (img.info)."""
    values = []
    for key, value in (img.info or {}).items():
        if not isinstance(key, str):
            continue
        key_lower = key.lower()
        if key_lower in _IMAGE_INFO_KEYS or key_lower.startswith("text"):
            decoded = _decode_exif_value(value)
            if decoded and len(decoded) < 10000:
                values.append(decoded)
    return values


def _exif_values(img) -> List[str]:
    """Camera, author and description fields from EXIF, plus the capture date."""
    if ExifTags is None or not hasattr(img, "getexif"):
        return []
    exif = img.getexif()
    if not exif:
        return []

    tags = dict(exif.items())
    # DateTimeOriginal and UserComment live in the Exif sub-IFD, not in IFD0.
    try:
        tags.update(exif.get_ifd(ExifTags.IFD.Exif))
    except Exception:
        pass

    values = []
    date_value = ""
    for tag_id, raw_value in tags.items():
        tag_name = ExifTags.TAGS.get(tag_id, "")
        if tag_name in _EXIF_TEXT_TAGS:
            decoded = _decode_exif_value(raw_value)
            if decoded:
                values.append(decoded)
        elif tag_name in _EXIF_DATE_TAGS and not date_value:
            # "2024:05:01 10:30:00" -> "2024-05-01". The time of day is left out
            # because tokens like "10" or "30" would match unrelated numeric queries.
            match = re.match(r"(\d{4}):(\d{2}):(\d{2})", _decode_exif_value(raw_value))
            if match:
                date_value = "-".join(match.groups())
    if date_value:
        values.append(date_value)
    return values


def _xmp_values(img) -> List[str]:
    """Title, description and keywords from an XMP packet (Lightroom, Photoshop, phones)."""
    packet = img.info.get("xmp") or img.info.get("XML:com.adobe.xmp")
    if not packet:
        return []
    if isinstance(packet, bytes):
        packet = packet.decode("utf-8", errors="ignore")
    packet = packet.strip("\x00 \n\r\t")
    try:
        root = ET.fromstring(packet)
    except ET.ParseError:
        return []

    values = []
    for elem in root.iter():
        if not elem.tag.startswith("{http://purl.org/dc/elements/1.1/}"):
            continue
        if elem.tag.split("}")[-1] not in ("title", "description", "subject"):
            continue
        for child in elem.iter():
            if child.text and child.text.strip():
                values.append(child.text.strip())
    return values


def _read_sidecar_caption(file_path: str) -> str:
    """Reads `photo.caption` or `photo.txt` stored next to `photo.jpg`, if present."""
    base = Path(file_path)
    for ext in (".caption", ".txt"):
        sidecar = base.with_suffix(ext)
        if sidecar == base or not sidecar.is_file():
            continue
        try:
            with open(sidecar, "rb") as f:
                text = f.read(SIDECAR_MAX_BYTES).decode("utf-8", errors="ignore").strip()
            if text:
                return text
        except OSError:
            pass
    return ""


def read_image_metadata(file_path: str) -> str:
    """Returns the searchable metadata of an image. Never raises."""
    parts: List[str] = []
    if Image is not None:
        try:
            with Image.open(file_path) as img:
                width, height = img.size
                fmt = (img.format or Path(file_path).suffix.lstrip(".")).upper()
                parts.append(f"[Image {fmt} {width}x{height}]")
                parts.extend(_embedded_text_values(img))
                parts.extend(_exif_values(img))
                parts.extend(_xmp_values(img))
        except Exception as e:
            logger.debug(f"Could not read image metadata for {file_path}: {e}")

    caption = _read_sidecar_caption(file_path)
    if caption:
        parts.append(caption)

    unique_parts = list(dict.fromkeys(part for part in parts if part))
    return "\n".join(unique_parts)



@lru_cache(maxsize=1)
def _installed_rapidocr_version() -> str:
    """Version of the installed `rapidocr` package, or "" if it is not installed."""
    if importlib.util.find_spec("rapidocr") is None:
        return ""
    try:
        return importlib.metadata.version("rapidocr")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def get_ocr_engine_id() -> str:
    """Names the OCR backend run_image_ocr will use, or "" if none.
    Stored per image in the catalog, so images are re-read when it changes.
    """
    if _custom_ocr_backend is not None:
        return "custom"
    if Image is not None and not _rapidocr_failed and _installed_rapidocr_version():
        return f"rapidocr-{_installed_rapidocr_version()}"
    if Image is not None and shutil.which("tesseract"):
        return "tesseract"
    return ""


def _get_rapidocr_engine():
    """Creates the shared RapidOCR engine on first use. Call with _rapidocr_lock held."""
    global _rapidocr_engine, _rapidocr_failed
    if _rapidocr_engine is None and not _rapidocr_failed:
        try:
            from rapidocr import RapidOCR  # type: ignore

            _rapidocr_engine = RapidOCR(
                params={"Global.log_level": "error", "Global.max_side_len": OCR_MAX_SIDE}
            )
        except Exception as e:
            _rapidocr_failed = True
            logger.warning(f"RapidOCR could not be started, OCR falls back to Tesseract if installed: {e}")
    return _rapidocr_engine


def _recognize_with_rapidocr(image) -> Optional[List[str]]:
    """Text lines in reading order, or None if RapidOCR is unavailable."""
    with _rapidocr_lock:
        engine = _get_rapidocr_engine()
        if engine is None:
            return None
        result = engine(image)
    return [text.strip() for text in (getattr(result, "txts", None) or ())]


def _recognize_with_tesseract(image) -> List[str]:
    tesseract_bin = shutil.which("tesseract")
    if not tesseract_bin:
        return []
    with tempfile.TemporaryDirectory() as tmpdir:
        image_path = os.path.join(tmpdir, "page.png")
        image.save(image_path)
        proc = subprocess.run(
            [tesseract_bin, image_path, "stdout", "-l", "eng", "--oem", "1"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    if proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines()]


def _recognize_lines(image) -> List[str]:
    lines = _recognize_with_rapidocr(image)
    if lines is None:
        lines = _recognize_with_tesseract(image)
    # Drop fragments without any letter or digit, such as a lone icon read as "□".
    return [line for line in lines if any(ch.isalnum() for ch in line)]



def _to_rgb(image):
    """Converts any Pillow mode to RGB so text keeps its contrast. Returns None if empty."""
    if image.mode in ("I", "I;16", "I;16B", "I;16L", "I;16N", "F"):
        # 16-bit and float images: stretch the used value range onto 0..255.
        image = image.convert("F")
        low, high = image.getextrema()
        if high <= low:
            return None
        scale = 255.0 / (high - low)
        return image.point(lambda v: (v - low) * scale).convert("L").convert("RGB")

    has_alpha = image.mode in ("RGBA", "LA", "PA") or (
        image.mode == "P" and "transparency" in image.info
    )
    if not has_alpha:
        return image.convert("RGB")

    # Transparent pixels usually store black, which would hide black text after RGB
    # conversion, so paste onto a background that contrasts with the visible pixels.
    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    if alpha.getextrema()[1] == 0:
        return None
    visible_mask = alpha.point(lambda a: 255 if a >= 128 else 0)
    if visible_mask.getextrema()[1] == 0:
        visible_mask = alpha.point(lambda a: 255 if a > 0 else 0)
    visible_brightness = ImageStat.Stat(rgba.convert("L"), mask=visible_mask).mean[0]
    background_colour = (0, 0, 0) if visible_brightness > 160 else (255, 255, 255)
    background = Image.new("RGB", rgba.size, background_colour)
    background.paste(rgba, mask=alpha)
    return background


def _prepare_page_for_ocr(page):
    """Returns an upright RGB copy of `page`, or None if it cannot contain text."""
    width, height = page.size
    if min(width, height) < MIN_OCR_SIDE or width * height > MAX_OCR_PIXELS:
        return None
    try:
        page = ImageOps.exif_transpose(page)
    except Exception:
        pass
    rgb = _to_rgb(page)
    if rgb is None:
        return None
    low, high = rgb.convert("L").getextrema()
    if high - low < MIN_OCR_CONTRAST:
        return None
    return rgb


def _reduce_large_jpeg(img) -> None:
    """Lets the JPEG decoder skip detail OCR does not use, which is much faster."""
    width, height = img.size
    long_side, short_side = max(width, height), min(width, height)
    if img.format != "JPEG" or long_side <= 2 * OCR_MAX_SIDE or long_side > short_side * TILE_ASPECT_RATIO:
        return
    scale = OCR_MAX_SIDE / long_side
    img.draft("RGB", (int(width * scale), int(height * scale)))


def _iter_ocr_pages(img):
    """Yields each page worth reading: every page of a multi-page TIFF, else the first frame."""
    page_count = getattr(img, "n_frames", 1) if img.format == "TIFF" else 1
    for page_index in range(min(page_count, MAX_OCR_PAGES)):
        try:
            img.seek(page_index)
            img.load()
        except Exception:
            return
        yield img


def _tile_ranges(length: int, tile_length: int) -> List[Tuple[int, int]]:
    """Splits 0..length into ranges of tile_length that overlap by 10%."""
    # With a 10% overlap each extra tile adds 0.9 tile lengths, so this keeps the
    # number of tiles at or below MAX_OCR_TILES.
    min_tile_length = math.ceil(length / (0.9 * (MAX_OCR_TILES - 1) + 1))
    tile_length = max(tile_length, min_tile_length)
    step = tile_length - tile_length // 10

    ranges = []
    start = 0
    while True:
        end = min(start + tile_length, length)
        ranges.append((start, end))
        if end >= length:
            return ranges
        start += step


def _split_into_tiles(image) -> list:
    """Splits long, narrow images into overlapping tiles, since the OCR engine would
    shrink them until their text is unreadable. Other images stay whole.
    """
    width, height = image.size
    if height > OCR_MAX_SIDE and height > width * TILE_ASPECT_RATIO:
        return [image.crop((0, top, width, bottom))
                for top, bottom in _tile_ranges(height, OCR_MAX_SIDE)]
    if width > OCR_MAX_SIDE and width > height * TILE_ASPECT_RATIO:
        return [image.crop((left, 0, right, height))
                for left, right in _tile_ranges(width, OCR_MAX_SIDE)]
    return [image]


def _ocr_page(page) -> List[str]:
    lines: List[str] = []
    previous_tile_lines: set = set()
    for tile in _split_into_tiles(page):
        tile_lines = _recognize_lines(tile)
        # Text inside the overlap between two tiles is read twice; keep one copy.
        lines.extend(line for line in tile_lines if line not in previous_tile_lines)
        previous_tile_lines = set(tile_lines)
    return lines


def run_image_ocr(file_path: str) -> str:
    """Returns the text visible in an image, or "". Never raises.
    Tries a registered backend, then RapidOCR, then the tesseract command.
    """
    if _custom_ocr_backend is not None:
        try:
            result = _custom_ocr_backend(file_path)
            return result.strip() if result else ""
        except Exception as e:
            logger.error(f"Custom OCR backend failed on {file_path}: {e}")
            return ""

    if Image is None or not get_ocr_engine_id():
        return ""

    page_texts: List[str] = []
    try:
        with Image.open(file_path) as img:
            if img.size[0] * img.size[1] > MAX_OCR_PIXELS:
                return ""
            _reduce_large_jpeg(img)
            for page in _iter_ocr_pages(img):
                prepared = _prepare_page_for_ocr(page)
                if prepared is not None:
                    page_texts.append("\n".join(_ocr_page(prepared)))
    except OSError as e:
        # Missing, unreadable, truncated or not really an image.
        logger.debug(f"Could not read image for OCR {file_path}: {e}")
    except Exception as e:
        logger.warning(f"OCR failed on {file_path}: {e}")

    if len(page_texts) > 1:
        return "\n".join(
            f"[Page {number}] {text}" for number, text in enumerate(page_texts, start=1) if text
        )
    return page_texts[0] if page_texts else ""


def combine_image_text(metadata: str, ocr_text: str) -> str:
    return "\n".join(part for part in (metadata, ocr_text) if part)


def extract_text_image(file_path: str, include_ocr: bool = True) -> str:
    """Searchable text of an image: its metadata, plus the OCR text when `include_ocr`."""
    metadata = read_image_metadata(file_path)
    if not include_ocr:
        return metadata
    return combine_image_text(metadata, run_image_ocr(file_path))


def _parse_timecode_to_seconds(tc: str) -> Optional[float]:
    """Parses `HH:MM:SS,mmm`, `HH:MM:SS.mmm`, or `MM:SS.xx` into seconds."""
    tc = tc.strip().replace(",", ".")
    parts = tc.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
    except ValueError:
        pass
    return None


def parse_subtitle_text(raw_content: str) -> str:
    """Converts SRT, WebVTT or LRC content into `[MM:SS] text` lines."""
    lines = raw_content.splitlines()
    entries: List[str] = []

    # Check for LRC format: `[01:23.45] Lyric line`
    lrc_pattern = re.compile(r"^\[(\d{1,2}:\d{2}(?:\.\d{1,3})?)\](.*)$")
    lrc_matches = 0
    for line in lines:
        m = lrc_pattern.match(line.strip())
        if m:
            secs = _parse_timecode_to_seconds(m.group(1))
            lyric = m.group(2).strip()
            if secs is not None and lyric:
                entries.append(f"{format_timestamp(secs)} {lyric}")
                lrc_matches += 1
    if lrc_matches > 0:
        return "\n".join(entries)

    # SRT / WebVTT timecode arrow pattern: `00:01:15,000 --> 00:01:18,500` or `01:15.000 --> 01:18.500`
    arrow_pattern = re.compile(
        r"((?:\d{1,2}:)?\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*((?:\d{1,2}:)?\d{2}:\d{2}[,.]\d{1,3})"
    )

    current_ts: Optional[str] = None
    current_lines: List[str] = []

    def flush_cue():
        if current_lines:
            text_joined = " ".join(current_lines)
            text_clean = re.sub(r"<[^>]+>", "", text_joined).strip()
            if text_clean:
                if current_ts:
                    entries.append(f"{current_ts} {text_clean}")
                else:
                    entries.append(text_clean)

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.upper().startswith("WEBVTT") or line.startswith("NOTE"):
            if not line and current_lines:
                flush_cue()
                current_lines = []
            continue

        # Skip pure numeric SRT cue counters
        if line.isdigit() and not current_lines:
            continue

        m = arrow_pattern.search(line)
        if m:
            if current_lines:
                flush_cue()
                current_lines = []
            secs = _parse_timecode_to_seconds(m.group(1))
            current_ts = format_timestamp(secs) if secs is not None else None
        else:
            current_lines.append(line)

    if current_lines:
        flush_cue()

    return "\n".join(entries)


def find_and_parse_sidecar_subtitles(media_path: str) -> str:
    """
    Looks for adjacent subtitle/transcript files matching a media file:
      e.g. `lecture.mp4` -> `lecture.srt`, `lecture.vtt`, `lecture.en.srt`, `lecture.lrc`
    """
    p = Path(media_path)
    parent = p.parent
    stem = p.stem

    if not parent.is_dir():
        return ""

    candidates: List[Path] = []
    for ext in (".srt", ".vtt", ".lrc"):
        direct = parent / f"{stem}{ext}"
        if direct.is_file():
            candidates.append(direct)

    # Also match language-tagged sidecars like `video.en.srt` or `video.eng.vtt`
    try:
        for item in sorted(parent.iterdir()):
            if (
                item.is_file()
                and item not in candidates
                and item.name.startswith(f"{stem}.")
                and item.suffix.lower() in (".srt", ".vtt", ".lrc")
            ):
                candidates.append(item)
    except OSError:
        pass

    parsed_chunks: List[str] = []
    for cand in candidates:
        try:
            raw = cand.read_text(encoding="utf-8", errors="ignore")
            parsed = parse_subtitle_text(raw)
            if parsed:
                parsed_chunks.append(parsed)
        except OSError:
            pass

    return "\n".join(parsed_chunks)


def _parse_id3_tags(data: bytes) -> List[str]:
    """
    Pure-Python binary parser for ID3v2.2, ID3v2.3, ID3v2.4 headers and ID3v1 trailers.
    Extracts Title, Artist, Album, Year, Genre, Comment, and Unsynced Lyrics/Transcripts.
    """
    tags: List[str] = []

    # 1. Check ID3v2 header at start of data
    if len(data) >= 10 and data[:3] == b"ID3":
        version_major = data[3]
        # Synchsafe 28-bit integer size
        tag_size = (
            ((data[6] & 0x7F) << 21)
            | ((data[7] & 0x7F) << 14)
            | ((data[8] & 0x7F) << 7)
            | (data[9] & 0x7F)
        )
        end_pos = min(len(data), 10 + tag_size)
        pos = 10

        frame_labels = {
            "TIT2": "Title",
            "TT2": "Title",
            "TPE1": "Artist",
            "TP1": "Artist",
            "TPE2": "AlbumArtist",
            "TALB": "Album",
            "TAL": "Album",
            "TYER": "Year",
            "TDRC": "Date",
            "TCON": "Genre",
            "COMM": "Comment",
            "COM": "Comment",
            "USLT": "Lyrics",
            "SYLT": "Lyrics",
            "TIT3": "Subtitle",
            "TXXX": "Info",
        }

        while pos + 10 <= end_pos:
            if version_major == 2:
                frame_id = data[pos : pos + 3].decode("latin-1", errors="ignore")
                frame_size = int.from_bytes(data[pos + 3 : pos + 6], "big")
                header_len = 6
            else:
                frame_id = data[pos : pos + 4].decode("latin-1", errors="ignore")
                raw_sz = data[pos + 4 : pos + 8]
                if version_major == 4:
                    frame_size = (
                        ((raw_sz[0] & 0x7F) << 21)
                        | ((raw_sz[1] & 0x7F) << 14)
                        | ((raw_sz[2] & 0x7F) << 7)
                        | (raw_sz[3] & 0x7F)
                    )
                else:
                    frame_size = int.from_bytes(raw_sz, "big")
                header_len = 10

            if not frame_id.strip("\x00") or frame_size <= 0 or pos + header_len + frame_size > end_pos:
                break

            payload = data[pos + header_len : pos + header_len + frame_size]
            pos += header_len + frame_size

            if frame_id in frame_labels and len(payload) > 1:
                encoding_byte = payload[0]
                content_bytes = payload[1:]

                # COMM and USLT have a 3-byte language code + null-terminated short descriptor
                if frame_id in ("COMM", "COM", "USLT") and len(content_bytes) > 4:
                    content_bytes = content_bytes[3:]
                    if encoding_byte in (0, 3):
                        sep = content_bytes.find(b"\x00")
                        if sep != -1:
                            content_bytes = content_bytes[sep + 1 :]
                    elif encoding_byte in (1, 2):
                        sep = content_bytes.find(b"\x00\x00")
                        if sep != -1:
                            content_bytes = content_bytes[sep + 2 :]

                if encoding_byte == 0:
                    text = content_bytes.decode("latin-1", errors="ignore")
                elif encoding_byte == 1:
                    text = content_bytes.decode("utf-16", errors="ignore")
                elif encoding_byte == 2:
                    text = content_bytes.decode("utf-16-be", errors="ignore")
                else:
                    text = content_bytes.decode("utf-8", errors="ignore")

                text = text.replace("\x00", " ").strip()
                if text:
                    tags.append(f"{frame_labels[frame_id]}: {text}")

    # 2. Check ID3v1 128-byte trailer at end of file
    if len(data) >= 128 and data[-128:-125] == b"TAG":
        v1 = data[-128:]
        title = v1[3:33].decode("latin-1", errors="ignore").rstrip("\x00").strip()
        artist = v1[33:63].decode("latin-1", errors="ignore").rstrip("\x00").strip()
        album = v1[63:93].decode("latin-1", errors="ignore").rstrip("\x00").strip()
        year = v1[93:97].decode("latin-1", errors="ignore").rstrip("\x00").strip()
        comment = v1[97:125].decode("latin-1", errors="ignore").rstrip("\x00").strip()
        for label, val in [
            ("Title", title),
            ("Artist", artist),
            ("Album", album),
            ("Year", year),
            ("Comment", comment),
        ]:
            if val:
                entry = f"{label}: {val}"
                if entry not in tags:
                    tags.append(entry)

    return tags


def _parse_wav_metadata(file_path: str) -> List[str]:
    """
    Extracts WAV audio properties (sample rate, channels, duration) and RIFF `LIST`/`INFO`
    metadata chunks (`INAM` Title, `IART` Artist, `IPRD` Album, `ICMT` Comment, `IGNR` Genre).
    """
    info: List[str] = []
    try:
        with wave.open(file_path, "rb") as wf:
            channels = wf.getnchannels()
            rate = wf.getframerate()
            frames = wf.getnframes()
            duration = frames / float(rate) if rate > 0 else 0.0
            info.append(
                f"[Audio WAV {rate}Hz {channels}ch {format_timestamp(duration)}]"
            )
    except Exception:
        pass

    # Parse RIFF LIST/INFO chunks directly from binary
    riff_labels = {
        b"INAM": "Title",
        b"IART": "Artist",
        b"IPRD": "Album",
        b"ICMT": "Comment",
        b"IGNR": "Genre",
        b"ICRD": "Date",
        b"ISBJ": "Subject",
    }
    try:
        with open(file_path, "rb") as f:
            raw = f.read(256 * 1024)
        for fourcc, label in riff_labels.items():
            idx = raw.find(fourcc)
            if idx != -1 and idx + 8 <= len(raw):
                sz = struct.unpack("<I", raw[idx + 4 : idx + 8])[0]
                if 0 < sz < 4096 and idx + 8 + sz <= len(raw):
                    val = (
                        raw[idx + 8 : idx + 8 + sz]
                        .decode("utf-8", errors="ignore")
                        .rstrip("\x00")
                        .strip()
                    )
                    if val:
                        info.append(f"{label}: {val}")
    except OSError:
        pass

    return info


def _parse_mp4_atoms(file_path: str) -> Tuple[List[str], List[str]]:
    """Reads MP4/M4A/MOV metadata and embedded subtitles. Returns (metadata_lines, subtitle_lines)."""
    meta_lines: List[str] = []
    sub_lines: List[str] = []

    try:
        file_size = os.path.getsize(file_path)
        with open(file_path, "rb") as f:
            # Read first 512KB and last 256KB (in case `moov` atom is at the end of the MP4)
            head = f.read(min(file_size, 512 * 1024))
            tail = b""
            if file_size > 512 * 1024:
                f.seek(max(512 * 1024, file_size - 256 * 1024))
                tail = f.read()
        buf = head + tail

        # 1. Extract duration from `mvhd` atom if present
        mvhd_idx = buf.find(b"mvhd")
        if mvhd_idx != -1 and mvhd_idx + 24 <= len(buf):
            version = buf[mvhd_idx + 4]
            if version == 0 and mvhd_idx + 24 <= len(buf):
                timescale, duration = struct.unpack(">II", buf[mvhd_idx + 16 : mvhd_idx + 24])
                if timescale > 0:
                    secs = duration / float(timescale)
                    meta_lines.append(f"Duration: {format_timestamp(secs)}")
            elif version == 1 and mvhd_idx + 32 <= len(buf):
                timescale, duration = struct.unpack(">IQ", buf[mvhd_idx + 24 : mvhd_idx + 32])
                if timescale > 0:
                    secs = duration / float(timescale)
                    meta_lines.append(f"Duration: {format_timestamp(secs)}")

        # 2. Extract iTunes/MP4 `data` atoms inside `©nam`, `©ART`, `©alb`, `©cmt`, `desc`
        atom_tags = {
            b"\xa9nam": "Title",
            b"\xa9ART": "Artist",
            b"\xa9alb": "Album",
            b"\xa9cmt": "Comment",
            b"\xa9day": "Year",
            b"\xa9gen": "Genre",
            b"desc": "Description",
            b"\xa9lyr": "Lyrics",
        }
        for marker, label in atom_tags.items():
            idx = buf.find(marker)
            if idx != -1:
                data_idx = buf.find(b"data", idx, min(len(buf), idx + 256))
                if data_idx != -1 and data_idx >= 4 and data_idx + 12 <= len(buf):
                    data_size = struct.unpack(">I", buf[data_idx - 4 : data_idx])[0]
                    if 16 < data_size < 16384 and data_idx - 4 + data_size <= len(buf):
                        payload = buf[data_idx + 12 : data_idx - 4 + data_size]
                        text = payload.decode("utf-8", errors="ignore").strip("\x00").strip()
                        if text:
                            meta_lines.append(f"{label}: {text}")

    except OSError:
        pass

    return meta_lines, sub_lines


def _parse_vorbis_comments(file_path: str) -> List[str]:
    """Extracts `KEY=VALUE` Vorbis comments from `.flac`, `.ogg`, and `.opus` files."""
    tags: List[str] = []
    try:
        with open(file_path, "rb") as f:
            raw = f.read(128 * 1024)
        pattern = re.compile(
            rb"(TITLE|ARTIST|ALBUM|COMMENT|DESCRIPTION|LYRICS|GENRE|DATE)=([^\x00-\x1f]{1,2000})",
            re.IGNORECASE,
        )
        for m in pattern.finditer(raw):
            k = m.group(1).decode("ascii", errors="ignore").title()
            v = m.group(2).decode("utf-8", errors="ignore").strip()
            if v:
                tags.append(f"{k}: {v}")
    except OSError:
        pass
    return tags


def run_audio_asr(file_path: str) -> str:
    """Transcribes speech with the first available backend: registered, faster_whisper,
    openai-whisper, then the whisper command. Returns "" if none works.
    """
    if _custom_asr_backend is not None:
        try:
            res = _custom_asr_backend(file_path)
            if res and res.strip():
                return res.strip()
        except Exception as e:
            logger.error(f"Custom ASR backend failed on {file_path}: {e}")

    # Option 2: faster-whisper
    try:
        from faster_whisper import WhisperModel  # type: ignore

        model = WhisperModel("tiny.en", device="cpu", compute_type="int8")
        segments, _ = model.transcribe(file_path, beam_size=1)
        lines = [
            f"{format_timestamp(seg.start)} {seg.text.strip()}"
            for seg in segments
            if seg.text and seg.text.strip()
        ]
        if lines:
            return "\n".join(lines)
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f"faster-whisper error on {file_path}: {e}")

    # Option 3: openai-whisper
    try:
        import whisper  # type: ignore

        model = whisper.load_model("tiny.en")
        result = model.transcribe(file_path, fp16=False)
        segments = result.get("segments", [])
        lines = [
            f"{format_timestamp(seg['start'])} {seg['text'].strip()}"
            for seg in segments
            if seg.get("text", "").strip()
        ]
        if lines:
            return "\n".join(lines)
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f"whisper error on {file_path}: {e}")

    return ""


def extract_text_audio(file_path: str) -> str:
    """Audio tags and sidecar transcripts, plus speech-to-text when no transcript exists."""
    ext = Path(file_path).suffix.lower()
    parts: List[str] = []

    if ext == ".wav":
        parts.extend(_parse_wav_metadata(file_path))

    # Check ID3 tags (common in .mp3 and sometimes .wav/.flac)
    try:
        file_size = os.path.getsize(file_path)
        with open(file_path, "rb") as f:
            head = f.read(min(file_size, 256 * 1024))
            tail = b""
            if file_size > 128:
                f.seek(max(0, file_size - 128))
                tail = f.read(128)
        id3_tags = _parse_id3_tags(head + tail)
        parts.extend(id3_tags)
    except OSError:
        pass

    if ext in (".flac", ".ogg", ".opus"):
        parts.extend(_parse_vorbis_comments(file_path))
    elif ext in (".m4a", ".aac", ".mp4"):
        mp4_meta, _ = _parse_mp4_atoms(file_path)
        parts.extend(mp4_meta)

    # Sidecar transcript / lyrics (.srt, .vtt, .lrc)
    sidecar_subs = find_and_parse_sidecar_subtitles(file_path)
    if sidecar_subs:
        parts.append(sidecar_subs)

    # Stage B: Speech-to-Text ASR
    asr_text = run_audio_asr(file_path)
    if asr_text:
        parts.append(asr_text)

    return "\n".join(parts)


def extract_text_video(file_path: str) -> str:
    """Container metadata and subtitles, plus speech-to-text when no subtitles exist."""
    ext = Path(file_path).suffix.lower()
    parts: List[str] = [f"[Video {ext.lstrip('.').upper()}]"]

    # 1. Container metadata (MP4/MOV atoms)
    if ext in (".mp4", ".mov", ".m4v"):
        mp4_meta, mp4_subs = _parse_mp4_atoms(file_path)
        parts.extend(mp4_meta)
        parts.extend(mp4_subs)

    # 2. Extract embedded subtitle track via `ffmpeg` if installed
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin:
        try:
            proc = subprocess.run(
                [ffmpeg_bin, "-v", "quiet", "-i", str(file_path), "-map", "0:s:0", "-f", "srt", "-"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                embedded_srt = parse_subtitle_text(proc.stdout)
                if embedded_srt:
                    parts.append(embedded_srt)
        except Exception:
            pass

    # 3. Sidecar subtitles (`.srt`, `.vtt`, `.lrc`, including `.en.srt`)
    sidecar_subs = find_and_parse_sidecar_subtitles(file_path)
    if sidecar_subs:
        parts.append(sidecar_subs)

    # 4. Stage B: Speech-to-Text ASR fallback
    asr_text = run_audio_asr(file_path)
    if asr_text:
        parts.append(asr_text)

    return "\n".join(parts)

