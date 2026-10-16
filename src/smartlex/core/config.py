import json
from pathlib import Path

CONFIG_FILE = Path("config.json")

DEFAULT_CONFIG = {
    "NUM_PROCESSES": 8,
    "TOP_KEYWORDS": 150,
    "AUTOCOMPLETE_WORDS": 200,
    "SUPPORTED_FORMATS": [
        # Office Documents & E-Books
        ".pdf",
        ".docx",
        ".xlsx",
        ".pptx",
        ".epub",
        ".odt",
        ".ods",
        ".odp",
        # Plain Text, Data & Source Code
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
        ".xml",
        ".yaml",
        ".yml",
        # Images & Screenshots (EXIF/Metadata + OCR)
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".bmp",
        ".tiff",
        ".gif",
        # Audio Recordings (ID3/RIFF/Vorbis/MP4 Tags + Transcripts/ASR)
        ".mp3",
        ".wav",
        ".flac",
        ".m4a",
        ".ogg",
        ".aac",
        # Video & Subtitle Tracks (Metadata + [MM:SS] Subtitles/ASR)
        ".mp4",
        ".mkv",
        ".mov",
        ".webm",
        ".avi",
        ".srt",
        ".vtt",
        ".lrc",
    ],
    "INDEX_FOLDER": "all",
    "OUTPUT_FILE": "output.json",
    "INDEX_FILE": "index.slex",
    "CATALOG_FILE": "smartlex_catalog.db",
    "AUTOCOMPLETE_FILE": "autocomplete_words.json",
}


def load_config() -> dict:
    """
    Loads configuration from `config.json` and merges it with `DEFAULT_CONFIG`.
    Normalizes keys to UPPERCASE so lowercase keys in `config.json` work seamlessly.
    """
    merged = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                user_config = json.load(f)
            for key, value in user_config.items():
                merged[key.upper()] = value
        except Exception:
            pass
    return merged
