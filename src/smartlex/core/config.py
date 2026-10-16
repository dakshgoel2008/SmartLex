import json
from pathlib import Path

CONFIG_FILE = Path("config.json")

DEFAULT_CONFIG = {
    "NUM_PROCESSES": 8,
    "TOP_KEYWORDS": 150,
    "AUTOCOMPLETE_WORDS": 200,
    "SUPPORTED_FORMATS": [
        ".pdf",
        ".docx",
        ".txt",
        ".md",
        ".csv",
        ".json",
        ".py",
        ".cpp",
        ".h",
        ".html",
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
    Normalizes keys to UPPERCASE so lowercase keys in `config.json`.
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
