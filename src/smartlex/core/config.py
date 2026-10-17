import json
from pathlib import Path

CONFIG_FILE = Path("config.json")
USER_DATA_DIR = Path.home() / ".smartlex"

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
        ".tif",
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


def get_app_data_dir() -> Path:
    """
    Returns `~/.smartlex` for global PyPI installations so SmartLex can be
    launched from any working directory without scattering index files.
    """
    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    return USER_DATA_DIR


def load_config() -> dict:
    """Loads ./config.json (in the repo) or ~/.smartlex/config.json, merged over DEFAULT_CONFIG."""
    merged = dict(DEFAULT_CONFIG)

    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                user_config = json.load(f)
            for key, value in user_config.items():
                merged[key.upper()] = value
        except Exception:
            pass
    else:
        # Installed globally via pip/PyPI: store index, catalog, and batches in ~/.smartlex/
        data_dir = get_app_data_dir()
        user_cfg_file = data_dir / "config.json"
        if user_cfg_file.exists():
            try:
                with open(user_cfg_file, "r", encoding="utf-8") as f:
                    user_config = json.load(f)
                for key, value in user_config.items():
                    merged[key.upper()] = value
            except Exception:
                pass

        for path_key in (
            "INDEX_FOLDER",
            "OUTPUT_FILE",
            "INDEX_FILE",
            "CATALOG_FILE",
            "AUTOCOMPLETE_FILE",
        ):
            val = Path(merged[path_key])
            if not val.is_absolute():
                merged[path_key] = str(data_dir / val)

    return merged
