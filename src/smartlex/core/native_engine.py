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
tokenize_keywords = _core.tokenize_keywords
stem_word = _core.stem_word
edit_distance = _core.edit_distance


class SmartLexEngine:
    """
    Unified Phase-1 Search & Indexing Engine combining:
    1. C++20 Compressed Positional Inverted Index (`_smartlex_core.NativeIndex`)
       for sub-millisecond BM25F ranking, exact phrase search, proximity scoring,
       and typo-tolerant fuzzy search.
    2. SQLite WAL Document Catalog (`CatalogDB`) for zero-reindexing incremental
       updates and instant Google-style context snippet generation.

    Also implements Python mapping methods (`__len__`, `__iter__`, `keys`, `values`,
    `items`) so legacy code expecting a dictionary continues to work seamlessly.
    """

    def __init__(
        self,
        index_file: str = "index.slex",
        catalog_file: str = "smartlex_catalog.db",
    ):
        self.index_file = str(index_file)
        self.catalog = CatalogDB(catalog_file)
        self.native_index = NativeIndex()

    def add_or_update_document(
        self,
        path: str,
        raw_text: str,
        mtime_ns: int = 0,
        file_size: int = 0,
        content_hash: str = "",
        save_to_catalog: bool = True,
    ) -> int:
        """Indexes a single document in C++ and optionally persists text to SQLite."""
        if mtime_ns == 0 and file_size == 0:
            stat_info = self.catalog.stat_file(path)
            if stat_info is not None:
                mtime_ns, file_size = stat_info

        doc_id = self.native_index.add_document(path, raw_text, mtime_ns, file_size)

        if save_to_catalog:
            if not content_hash:
                content_hash = self.catalog.compute_file_hash(path)
            self.catalog.upsert_documents_batch(
                [(path, mtime_ns, file_size, content_hash, raw_text)]
            )
        return doc_id

    def add_documents_batch(
        self, records: List[Tuple[str, int, int, str, str]]
    ) -> int:
        """
        Adds a batch of (path, mtime_ns, file_size, content_hash, raw_text) records
        into both the C++ Inverted Index and the SQLite WAL Catalog.
        """
        if not records:
            return 0

        for path, mtime_ns, file_size, _content_hash, raw_text in records:
            self.native_index.add_document(path, raw_text, mtime_ns, file_size)

        self.catalog.upsert_documents_batch(records)
        return len(records)

    def remove_documents(self, paths: Iterable[str]) -> int:
        """Removes deleted files from both the C++ index (via O(1) tombstone) and catalog."""
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
        """Loads the compressed binary `.slex` index from disk."""
        target = str(index_file or self.index_file)
        if Path(target).exists():
            if self.native_index.load_binary(target):
                return True
        return False

    # --- Dictionary-compatible interface for legacy callers ---

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

