import hashlib
import os
import re
import sqlite3
import time
import zlib
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


class CatalogDB:
    """
    SQLite-backed Document Catalog & Compressed Snippet Store (WAL Mode).

    Responsibilities:
    1. Tracks (path, mtime_ns, file_size, content_hash) so SmartLex NEVER
       re-indexes files that haven't changed.
    2. Stores zlib-compressed extracted text so SmartLex can generate
       Google-style context snippets ("...total due for invoice 2024 is...")
       in < 0.2ms without re-opening the PDF or DOCX file during search.
    """

    def __init__(self, db_path: str = "smartlex_catalog.db"):
        self.db_path = str(db_path)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_db(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    path TEXT PRIMARY KEY,
                    mtime_ns INTEGER NOT NULL,
                    file_size INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    compressed_text BLOB,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.commit()

    @staticmethod
    def stat_file(path: str) -> Optional[Tuple[int, int]]:
        """Returns (mtime_ns, file_size) for a file, or None if inaccessible."""
        try:
            st = os.stat(path)
            return st.st_mtime_ns, st.st_size
        except OSError:
            return None

    @staticmethod
    def compute_file_hash(path: str) -> str:
        """Computes a fast 16-byte BLAKE2b digest of a file's contents."""
        hasher = hashlib.blake2b(digest_size=16)
        try:
            with open(path, "rb") as f:
                while chunk := f.read(65536):
                    hasher.update(chunk)
            return hasher.hexdigest()
        except OSError:
            return ""

    def get_all_metadata(self) -> Dict[str, Tuple[int, int, str]]:
        """Returns {path: (mtime_ns, file_size, content_hash)} for all cataloged files."""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT path, mtime_ns, file_size, content_hash FROM documents"
            )
            return {row[0]: (row[1], row[2], row[3]) for row in cursor.fetchall()}

    def plan_incremental_sync(
        self, current_paths: Iterable[str]
    ) -> Tuple[List[str], List[str], List[str]]:
        """
        Compares current files on disk against the catalog.

        Returns:
            (files_to_index, unchanged_files, deleted_files)
        """
        existing_meta = self.get_all_metadata()
        seen_paths = set()

        files_to_index: List[str] = []
        unchanged_files: List[str] = []

        for path in current_paths:
            seen_paths.add(path)
            stat_info = self.stat_file(path)
            if stat_info is None:
                continue

            mtime_ns, file_size = stat_info
            prev = existing_meta.get(path)

            if prev is not None and prev[0] == mtime_ns and prev[1] == file_size:
                unchanged_files.append(path)
            else:
                files_to_index.append(path)

        deleted_files = [p for p in existing_meta.keys() if p not in seen_paths]
        return files_to_index, unchanged_files, deleted_files

    def upsert_documents_batch(
        self, records: List[Tuple[str, int, int, str, str]]
    ) -> None:
        """
        Saves a batch of indexed documents into the SQLite catalog.
        Each tuple in `records` is: (path, mtime_ns, file_size, content_hash, raw_text).
        """
        if not records:
            return

        now = time.time()
        rows = []
        for path, mtime_ns, file_size, content_hash, raw_text in records:
            compressed = (
                zlib.compress(raw_text.encode("utf-8", errors="ignore"), level=6)
                if raw_text
                else b""
            )
            rows.append((path, mtime_ns, file_size, content_hash, compressed, now))

        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO documents (path, mtime_ns, file_size, content_hash, compressed_text, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    mtime_ns = excluded.mtime_ns,
                    file_size = excluded.file_size,
                    content_hash = excluded.content_hash,
                    compressed_text = excluded.compressed_text,
                    updated_at = excluded.updated_at
                """,
                rows,
            )
            conn.commit()

    def remove_documents(self, paths: List[str]) -> None:
        """Removes deleted file paths from the catalog."""
        if not paths:
            return
        with self._connect() as conn:
            conn.executemany(
                "DELETE FROM documents WHERE path = ?", [(p,) for p in paths]
            )
            conn.commit()

    def get_document_text(self, path: str) -> str:
        """Decompresses and returns the stored plain text for a document."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT compressed_text FROM documents WHERE path = ?", (path,)
            ).fetchone()
            if not row or not row[0]:
                return ""
            try:
                return zlib.decompress(row[0]).decode("utf-8", errors="ignore")
            except Exception:
                return ""

    def iter_all_documents(self) -> Iterable[Tuple[str, int, int, str]]:
        """Yields (path, mtime_ns, file_size, plain_text) for all stored documents."""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT path, mtime_ns, file_size, compressed_text FROM documents"
            )
            for path, mtime_ns, file_size, blob in cursor:
                text = ""
                if blob:
                    try:
                        text = zlib.decompress(blob).decode("utf-8", errors="ignore")
                    except Exception:
                        text = ""
                yield path, mtime_ns, file_size, text

    def generate_snippet(
        self, path: str, matched_words: List[str], max_length: int = 160
    ) -> str:
        """
        Extracts a concise, Google-style context snippet around the first occurrence
        of any matched query word in the document.
        """
        text = self.get_document_text(path)
        if not text:
            return ""

        # Collapse excessive whitespace for a clean single-line snippet
        clean_text = re.sub(r"\s+", " ", text).strip()
        if not clean_text:
            return ""

        lower_text = clean_text.lower()
        best_idx = -1

        for word in matched_words:
            w = word.lower().strip()
            if not w:
                continue
            idx = lower_text.find(w)
            if idx != -1 and (best_idx == -1 or idx < best_idx):
                best_idx = idx

        if best_idx == -1:
            return (
                clean_text[:max_length] + "..."
                if len(clean_text) > max_length
                else clean_text
            )

        half_window = max_length // 2
        start = max(0, best_idx - half_window)
        end = min(len(clean_text), best_idx + half_window)

        # Snap to word boundaries where possible
        if start > 0:
            space_idx = clean_text.find(" ", start)
            if 0 <= space_idx < best_idx:
                start = space_idx + 1

        snippet = clean_text[start:end].strip()
        prefix = "..." if start > 0 else ""
        suffix = "..." if end < len(clean_text) else ""
        return f"{prefix}{snippet}{suffix}"

