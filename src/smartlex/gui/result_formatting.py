"""
Qt-free helpers for the SmartLex search window (Phase 4).

Everything in this module is plain Python so it can be unit-tested without a
display or PyQt5. The GUI widgets in `widgets.py` and `main_window.py` only
call these helpers to decide *what* to show; they never re-implement the logic.

Responsibilities:
  1. Filter pills  -> which `type:` query token each pill adds.
  2. File details  -> icon, friendly type name, size and modified time for a result card.
  3. Snippets      -> safe HTML with the matched query words highlighted.
  4. Progress      -> reading "Indexing contents: X/Y files" messages for the progress bar.
"""

import html
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Tuple

# ==============================================================================
# 1. FILE CATEGORIES & FILTER PILLS
# ==============================================================================

# NOTE: These extension sets MUST stay identical to the `type:` categories in
# `cpp/src/inverted_index.cpp` (InvertedIndex::parse_query). The Phase 4 unit
# tests index one file per extension and verify both sides agree.
CATEGORY_EXTENSIONS = {
    "doc": {".pdf", ".docx", ".doc", ".odt", ".rtf", ".txt", ".md", ".epub", ".rst"},
    "sheet": {".xlsx", ".xls", ".csv", ".tsv", ".ods"},
    "slide": {".pptx", ".ppt", ".odp"},
    "image": {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif", ".gif", ".svg"},
    "audio": {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac", ".wma", ".opus"},
    "video": {".mp4", ".mkv", ".mov", ".webm", ".avi", ".flv", ".wmv", ".m4v"},
    "code": {
        ".py", ".cpp", ".c", ".h", ".hpp", ".js", ".ts", ".html", ".htm", ".css",
        ".json", ".xml", ".yaml", ".yml", ".sh", ".rs", ".go", ".java",
    },
}

# Singular, human-friendly names shown on result cards (e.g. "PDF Document")
CATEGORY_DISPLAY_NAMES = {
    "doc": "Document",
    "sheet": "Spreadsheet",
    "slide": "Presentation",
    "image": "Image",
    "audio": "Audio",
    "video": "Video",
    "code": "Code",
    "": "File",
}

CATEGORY_ICONS = {
    "doc": "📄",
    "sheet": "📊",
    "slide": "📽️",
    "image": "🖼️",
    "audio": "🎵",
    "video": "🎬",
    "code": "💻",
    "": "📁",
}


@dataclass(frozen=True)
class FilterCategory:
    """One filter pill shown under the search bar."""

    key: str    # C++ `type:` category name, or "" for the "All" pill
    label: str  # Text shown on the pill

    @property
    def icon(self) -> str:
        return CATEGORY_ICONS.get(self.key, CATEGORY_ICONS[""])


# Order in which the pills appear in the UI
FILTER_CATEGORIES = (
    FilterCategory("", "All"),
    FilterCategory("doc", "Docs"),
    FilterCategory("sheet", "Sheets"),
    FilterCategory("slide", "Slides"),
    FilterCategory("image", "Images"),
    FilterCategory("audio", "Audio"),
    FilterCategory("video", "Video"),
    FilterCategory("code", "Code"),
)

# Matches an `ext:` or `type:` operator typed by the user (same rule as the C++
# parser: the word must START with the operator and have a value after the colon).
_USER_TYPE_FILTER_PATTERN = re.compile(r"(?:^|\s)(?:ext|type):\S", re.IGNORECASE)


def build_search_query(user_text: str, category_key: str) -> str:
    """
    Combines what the user typed with the selected filter pill.

    Examples:
        ("invoice 2024", "")       -> "invoice 2024"
        ("invoice 2024", "doc")    -> "invoice 2024 type:doc"
        ("", "image")              -> "type:image"          (lists all images)
        ("logo ext:svg", "image")  -> "logo ext:svg"        (user's own filter wins)

    Why the user's filter wins: the C++ engine treats several `ext:`/`type:`
    operators as a UNION of extensions, so adding the pill on top would widen
    the results instead of narrowing them.
    """
    query = user_text.strip()
    if not category_key:
        return query
    if _USER_TYPE_FILTER_PATTERN.search(query):
        return query
    return f"{query} type:{category_key}".strip()


def get_file_category(file_path: str) -> str:
    """Returns the category key ("doc", "image", ...) for a path, or "" if unknown."""
    extension = Path(file_path).suffix.lower()
    for category_key, extensions in CATEGORY_EXTENSIONS.items():
        if extension in extensions:
            return category_key
    return ""


# ==============================================================================
# 2. RESULT CARD DETAILS (name, folder, type, size, modified time)
# ==============================================================================


@dataclass(frozen=True)
class FileDetails:
    """Everything a result card needs to display about one file."""

    name: str
    folder: str
    icon: str
    type_name: str      # e.g. "PDF Document", "MP3 Audio"
    size_text: str      # e.g. "1.4 MB" ("" if the file is missing)
    modified_text: str  # e.g. "16 Oct 2026, 21:24" ("" if the file is missing)
    exists: bool


def format_file_size(size_bytes: int) -> str:
    """Formats a byte count as a short human-readable string (e.g. "1.5 KB")."""
    if size_bytes < 1024:
        return f"{size_bytes} B"

    size = float(size_bytes)
    for unit in ("KB", "MB", "GB", "TB"):
        size /= 1024.0
        if size < 1024.0 or unit == "TB":
            return f"{size:.1f} {unit}"
    return f"{size:.1f} TB"  # unreachable, keeps type-checkers happy


def format_modified_time(epoch_seconds: float) -> str:
    """Formats a modification timestamp in local time (e.g. "16 Oct 2026, 21:24")."""
    return time.strftime("%d %b %Y, %H:%M", time.localtime(epoch_seconds))


def describe_file(file_path: str) -> FileDetails:
    """Collects display details for a search result (one cheap `os.stat` call)."""
    path = Path(file_path)
    category_key = get_file_category(file_path)

    extension_label = path.suffix.lstrip(".").upper()
    category_name = CATEGORY_DISPLAY_NAMES[category_key]
    type_name = f"{extension_label} {category_name}" if extension_label else category_name

    try:
        stat_result = os.stat(file_path)
        size_text = format_file_size(stat_result.st_size)
        modified_text = format_modified_time(stat_result.st_mtime)
        exists = True
    except OSError:
        size_text = ""
        modified_text = ""
        exists = False

    return FileDetails(
        name=path.name,
        folder=str(path.parent),
        icon=CATEGORY_ICONS[category_key],
        type_name=type_name,
        size_text=size_text,
        modified_text=modified_text,
        exists=exists,
    )


# ==============================================================================
# 3. SNIPPET HIGHLIGHTING
# ==============================================================================

HIGHLIGHT_STYLE = "background-color:#fff3b0; color:#1f2d3d; font-weight:600;"


def highlight_snippet_html(snippet: str, matched_words: Iterable[str]) -> str:
    """
    Converts a plain-text snippet into safe HTML where every matched query word
    is highlighted.

    - The snippet text is HTML-escaped, so file contents can never inject markup.
    - Matching happens on the ORIGINAL text (before escaping), so a query word
      like "amp" never matches inside an escaped "&amp;".
    - Words match as prefixes at a word boundary, so a stemmed match such as
      "comput" also highlights "computing" and "computers".
    """
    if not snippet:
        return ""

    # Longest words first so "network" wins over "net" when both matched
    unique_words = {word.strip().lower() for word in matched_words if word and word.strip()}
    if not unique_words:
        return html.escape(snippet)

    alternatives = "|".join(re.escape(word) for word in sorted(unique_words, key=len, reverse=True))
    word_pattern = re.compile(
        rf"(?<![0-9A-Za-z])(?:{alternatives})[0-9A-Za-z]*",
        re.IGNORECASE,
    )

    html_pieces = []
    last_end = 0
    for match in word_pattern.finditer(snippet):
        html_pieces.append(html.escape(snippet[last_end : match.start()]))
        html_pieces.append(
            f'<span style="{HIGHLIGHT_STYLE}">{html.escape(match.group(0))}</span>'
        )
        last_end = match.end()
    html_pieces.append(html.escape(snippet[last_end:]))
    return "".join(html_pieces)


# ==============================================================================
# 4. INDEXING PROGRESS
# ==============================================================================

# Matches the message emitted by `processor.process_all_batches()`:
#   "Indexing contents: 64/250 files (12 unchanged skipped)..."
_INDEXING_PROGRESS_PATTERN = re.compile(r"^Indexing contents: (\d+)/(\d+) files")


def parse_indexing_progress(message: str) -> Optional[Tuple[int, int]]:
    """
    Returns `(files_done, files_total)` from an indexing progress message,
    or None for messages without a count (scanning, saving, ...).
    """
    match = _INDEXING_PROGRESS_PATTERN.match(message.strip())
    if not match:
        return None
    files_done, files_total = int(match.group(1)), int(match.group(2))
    if files_total <= 0:
        return None
    return min(files_done, files_total), files_total

