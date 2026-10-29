import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from smartlex.core.catalog import CatalogDB
from smartlex.core.logger import setup_logger

logger = setup_logger(__name__)


def _load_cpp_core():
    """
    Loads the compiled C++20 extension `_smartlex_core`.
    If not yet built in a fresh developer checkout, compiles it automatically.
    """
    try:
        from smartlex.core import _smartlex_core
        return _smartlex_core
    except ImportError:
        repo_root = Path(__file__).resolve().parents[3]
        setup_py = repo_root / "setup.py"
        if setup_py.exists():
            logger.info("Building C++20 _smartlex_core extension module...")
            subprocess.check_call(
                [sys.executable, str(setup_py), "build_ext", "--inplace"],
                cwd=str(repo_root),
            )
            from smartlex.core import _smartlex_core
            return _smartlex_core
        raise


_core = _load_cpp_core()
NativeIndex = _core.NativeIndex
scan_directories = _core.scan_directories
tokenize_keywords = _core.tokenize_keywords
stem_word = _core.stem_word
edit_distance = _core.edit_distance


def _clean_text(text: str) -> str:
    """The C++ core takes NUL-terminated strings, so a NUL byte inside text would be
    rejected. NUL bytes are common in EXIF fields and UTF-16 files read as UTF-8."""
    if text and "\x00" in text:
        return text.replace("\x00", " ")
    return text or ""


class SmartLexEngine:
    """Search engine combining the C++ inverted index with the SQLite catalog."""

    def __init__(
        self,
        index_file: str = "index.slex",
        catalog_file: str = "smartlex_catalog.db",
    ):
        self.index_file = str(index_file)
        self.catalog = CatalogDB(catalog_file)
        self.native_index = NativeIndex()

    def index_filenames_tier0(self, paths: Iterable[str]) -> int:
        """Adds new paths with empty content so they are searchable by name before extraction."""
        existing = set(self.native_index.get_all_paths())
        added = 0
        for p in paths:
            if p not in existing:
                self.native_index.add_document(p, "", 0, 0)
                added += 1
        return added

    def add_or_update_document(
        self,
        path: str,
        raw_text: str,
        mtime_ns: int = 0,
        file_size: int = 0,
        content_hash: str = "",
        save_to_catalog: bool = True,
        ocr_engine: str = "",
        asr_engine: str = "",
    ) -> int:
        """Indexes a single document in C++ and optionally persists text to SQLite."""
        if mtime_ns == 0 and file_size == 0:
            stat_info = self.catalog.stat_file(path)
            if stat_info is not None:
                mtime_ns, file_size = stat_info

        raw_text = _clean_text(raw_text)
        doc_id = self.native_index.add_document(path, raw_text, mtime_ns, file_size)

        if save_to_catalog:
            if not content_hash:
                content_hash = self.catalog.compute_file_hash(path)
            self.catalog.upsert_documents_batch(
                [(path, mtime_ns, file_size, content_hash, raw_text)],
                ocr_engine=ocr_engine,
                asr_engine=asr_engine,
            )
        return doc_id

    def rename_or_move_document(
        self,
        old_path: str,
        new_path: str,
        new_mtime_ns: int = 0,
        file_size: int = 0,
    ) -> bool:
        """Moves a document to a new path, reusing its stored text instead of re-extracting it."""
        cached_text = self.catalog.rename_document(old_path, new_path, new_mtime_ns)
        if cached_text is None:
            # Fallback if old_path wasn't in SQLite catalog
            return self.native_index.rename_document(old_path, new_path)

        old_name = os.path.basename(old_path).lower()
        new_name = os.path.basename(new_path).lower()
        if old_name == new_name:
            if self.native_index.rename_document(old_path, new_path):
                return True

        self.native_index.remove_document(old_path)
        self.native_index.add_document(new_path, _clean_text(cached_text), new_mtime_ns, file_size)
        return True

    def add_documents_batch(
        self,
        records: List[Tuple[str, int, int, str, str]],
        ocr_engine: str = "",
        asr_engine: str = "",
    ) -> int:
        """Indexes (path, mtime_ns, file_size, content_hash, raw_text) records and saves them.
        `ocr_engine` and `asr_engine` name the backends that read them ("" = not read yet).
        """
        if not records:
            return 0

        cleaned = [
            (path, mtime_ns, file_size, content_hash, _clean_text(raw_text))
            for path, mtime_ns, file_size, content_hash, raw_text in records
        ]
        for path, mtime_ns, file_size, _content_hash, raw_text in cleaned:
            self.native_index.add_document(path, raw_text, mtime_ns, file_size)

        self.catalog.upsert_documents_batch(cleaned, ocr_engine=ocr_engine, asr_engine=asr_engine)
        return len(cleaned)

    def remove_documents(self, paths: Iterable[str]) -> int:
        """Removes deleted files from both the C++ index (as tombstones) and the catalog."""
        path_list = list(paths)
        removed = 0
        for p in path_list:
            if self.native_index.remove_document(p):
                removed += 1
        if path_list:
            self.catalog.remove_documents(path_list)
        return removed

    def search_detailed(
        self, query: str, max_results: int = 100, with_snippets: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Executes a full search in C++ and attaches context snippets from SQLite.
        """
        if not query or not query.strip():
            return []

        raw_results = self.native_index.search(query, max_results)
        if with_snippets:
            for item in raw_results:
                item["snippet"] = self.catalog.generate_snippet(
                    item["path"], item.get("matched_words", [])
                )
        return raw_results

    def search(
        self, query: str, max_results: int = 100, with_snippets: bool = True
    ) -> List[Dict[str, Any]]:
        """Alias for `search_detailed` returning ranked result dicts with snippets."""
        return self.search_detailed(query, max_results=max_results, with_snippets=with_snippets)

    def search_paths(self, query: str, max_results: int = 100) -> List[str]:
        """Executes a search in C++ and returns ranked file paths."""
        if not query or not query.strip():
            return []
        return self.native_index.search_paths(query, max_results)

    def suggest_autocomplete(self, prefix: str = "", max_words: int = 100) -> List[str]:
        """Returns top vocabulary words from the C++ index for autocomplete."""
        return self.native_index.suggest_autocomplete(prefix, max_words)

    def save(self, index_file: Optional[str] = None) -> bool:
        """Saves the compressed binary `.slex` index to disk."""
        target = str(index_file or self.index_file)
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        return self.native_index.save_binary(target)

    def load(self, index_file: Optional[str] = None) -> bool:
        """Loads the .slex index, then adds catalog rows written after it was last saved."""
        target = str(index_file or self.index_file)
        if not Path(target).exists():
            return False
        saved_at = os.path.getmtime(target)
        if not self.native_index.load_binary(target):
            return False
        for path, mtime_ns, file_size, text in self.catalog.iter_documents_updated_after(saved_at):
            if os.path.exists(path):
                self.native_index.add_document(path, _clean_text(text), mtime_ns, file_size)
        return True

    # Dictionary-style interface for legacy callers

    def __len__(self) -> int:
        return self.native_index.active_document_count()

    def __bool__(self) -> bool:
        return self.native_index.active_document_count() > 0

    def __contains__(self, path: str) -> bool:
        return path in set(self.native_index.get_all_paths())

    def __iter__(self) -> Iterator[str]:
        return iter(self.native_index.get_all_paths())

    def keys(self) -> List[str]:
        return self.native_index.get_all_paths()

    def __getitem__(self, path: str) -> List[str]:
        return self.native_index.get_document_keywords(path, 150)

    def values(self) -> Iterator[List[str]]:
        for path in self.native_index.get_all_paths():
            yield self.native_index.get_document_keywords(path, 150)

    def items(self) -> Iterator[Tuple[str, List[str]]]:
        for path in self.native_index.get_all_paths():
            yield path, self.native_index.get_document_keywords(path, 150)
