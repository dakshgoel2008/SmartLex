import json
from collections import Counter
from pathlib import Path
from typing import Any, List, Union

from smartlex.core.native_engine import NativeIndex, SmartLexEngine


def _resolve_binary_path(output_file: str) -> Path:
    path = Path(output_file)
    if path.suffix.lower() == ".slex":
        return path
    return path.with_suffix(".slex")


def save_index(
    data: Union[SmartLexEngine, NativeIndex, dict],
    output_file: str = "index.slex",
) -> bool:
    """
    Saves the search index to disk.
    - When `data` is a `SmartLexEngine` or `NativeIndex`, writes the compressed
      binary `.slex` format.
    - When `data` is a legacy dictionary and `output_file` ends with `.json`,
      saves both the JSON and the upgraded `.slex` binary index.
    """
    bin_path = _resolve_binary_path(output_file)

    if isinstance(data, SmartLexEngine):
        return data.save(str(bin_path))

    if isinstance(data, NativeIndex):
        bin_path.parent.mkdir(parents=True, exist_ok=True)
        return data.save_binary(str(bin_path))

    if isinstance(data, dict):
        # Build binary index alongside legacy JSON
        engine = SmartLexEngine(index_file=str(bin_path))
        for file_path, kws in data.items():
            text = " ".join(kws) if isinstance(kws, (list, tuple, set)) else str(kws)
            engine.add_or_update_document(file_path, text, save_to_catalog=False)
        engine.save(str(bin_path))

        if str(output_file).lower().endswith(".json"):
            Path(output_file).parent.mkdir(parents=True, exist_ok=True)
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        return True

    return False


def load_index(
    output_file: str = "index.slex",
    catalog_file: str = "smartlex_catalog.db",
) -> SmartLexEngine:
    """
    Loads the compressed C++20 `SmartLexEngine` from disk.
    Automatically migrates a legacy `output.json` file if `.slex` does not exist yet.
    """
    bin_path = _resolve_binary_path(output_file)
    engine = SmartLexEngine(index_file=str(bin_path), catalog_file=catalog_file)

    if bin_path.exists() and engine.load(str(bin_path)):
        return engine

    # Automatic migration from legacy output.json if present
    json_path = Path(output_file)
    if json_path.exists() and json_path.suffix.lower() == ".json":
        with open(json_path, "r", encoding="utf-8") as f:
            legacy_dict = json.load(f)
        if isinstance(legacy_dict, dict):
            for file_path, kws in legacy_dict.items():
                text = " ".join(kws) if isinstance(kws, list) else str(kws)
                engine.add_or_update_document(file_path, text, save_to_catalog=False)
            engine.save(str(bin_path))

    return engine


def generate_autocomplete(data: Any, top_n: int = 200) -> List[str]:
    """
    Generates the top `top_n` autocomplete keywords ranked by corpus frequency.
    """
    if isinstance(data, (SmartLexEngine, NativeIndex)):
        return data.suggest_autocomplete("", top_n)

    if isinstance(data, dict):
        words = []
        for kws in data.values():
            if isinstance(kws, (list, tuple, set)):
                words.extend(kws)
        freq = Counter(words)
        return [w for w, _ in freq.most_common(top_n)]

    return []
