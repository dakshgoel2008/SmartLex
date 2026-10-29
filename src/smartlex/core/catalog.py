import hashlib
import os
import re
import sqlite3
import time
import zlib
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple


class CatalogDB:
    """SQLite catalog of indexed files: change tracking, move detection and compressed text for snippets."""

    def __init__(self, db_path: str = "smartlex_catalog.db"):
        self.db_path = str(db_path)
        self._init_db()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Opens a connection that commits on success, rolls back on error and is always closed."""
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            with conn:
                yield conn
        finally:
            conn.close()

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
                    updated_at REAL NOT NULL,
                    ocr_engine TEXT NOT NULL DEFAULT '',
                    asr_engine TEXT NOT NULL DEFAULT ''
                )
                """
            )
            # Catalogs created by older versions lack these columns.
            columns = {row[1] for row in conn.execute("PRAGMA table_info(documents)")}
            for column in ("ocr_engine", "asr_engine"):
                if column not in columns:
                    conn.execute(
                        f"ALTER TABLE documents ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
                    )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_documents_updated_at ON documents(updated_at)"
            )
            # Keyed by content so copies and re-saved files are never transcribed twice.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS transcripts (
                    content_hash TEXT NOT NULL,
                    asr_engine TEXT NOT NULL,
                    compressed_text BLOB,
                    PRIMARY KEY (content_hash, asr_engine)
                )
                """
            )

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
        """Returns (files_to_index, unchanged_files, deleted_files) for the given paths."""
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

    def detect_moves_and_plan_sync(
        self, current_paths: Iterable[str]
    ) -> Tuple[List[str], List[str], List[str], List[Tuple[str, str, int, int]]]:
        """Like plan_incremental_sync, plus moved files matched by size and content hash.
        Returns (files_to_index, unchanged, deleted, moved) with moved as (old, new, mtime_ns, size).
        """
        existing_meta = self.get_all_metadata()
        seen_paths = set()

        candidate_new: List[Tuple[str, int, int]] = []
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
                candidate_new.append((path, mtime_ns, file_size))

        raw_deleted = [p for p in existing_meta.keys() if p not in seen_paths]

        # Build lookup of deleted files by (file_size, content_hash) -> list of old_paths
        deleted_by_size_and_hash: Dict[Tuple[int, str], List[str]] = {}
        deleted_sizes = set()
        for old_path in raw_deleted:
            _old_mtime, old_size, old_hash = existing_meta[old_path]
            if old_size > 0 and old_hash:
                deleted_sizes.add(old_size)
                deleted_by_size_and_hash.setdefault((old_size, old_hash), []).append(old_path)

        files_to_index: List[str] = []
        moved_files: List[Tuple[str, str, int, int]] = []
        matched_old_paths = set()

        for new_path, new_mtime_ns, new_size in candidate_new:
            # Only compute hash to check for move if new_path wasn't already in catalog
            # and its size matches at least one deleted file
            if new_path not in existing_meta and new_size in deleted_sizes:
                new_hash = self.compute_file_hash(new_path)
                matching_olds = deleted_by_size_and_hash.get((new_size, new_hash))
                if matching_olds:
                    old_path = matching_olds.pop(0)
                    matched_old_paths.add(old_path)
                    moved_files.append((old_path, new_path, new_mtime_ns, new_size))
                    continue

            files_to_index.append(new_path)

        deleted_files = [p for p in raw_deleted if p not in matched_old_paths]
        return files_to_index, unchanged_files, deleted_files, moved_files

    def rename_document(
        self, old_path: str, new_path: str, new_mtime_ns: int = 0
    ) -> Optional[str]:
        """
        Moves a catalog row to `new_path` without re-extracting the file and returns
        its stored text, or None if `old_path` is not cataloged.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT mtime_ns, compressed_text FROM documents WHERE path = ?",
                (old_path,),
            ).fetchone()
            if not row:
                return None

            old_mtime, blob = row
            final_mtime = new_mtime_ns if new_mtime_ns > 0 else old_mtime
            if new_path != old_path:
                conn.execute("DELETE FROM documents WHERE path = ?", (new_path,))
            conn.execute(
                "UPDATE documents SET path = ?, mtime_ns = ?, updated_at = ? WHERE path = ?",
                (new_path, final_mtime, time.time(), old_path),
            )

        return self._decompress(blob)

    @staticmethod
    def _compress(text: str) -> bytes:
        if not text:
            return b""
        return zlib.compress(text.encode("utf-8", errors="ignore"), level=6)

    @staticmethod
    def _decompress(blob) -> str:
        if not blob:
            return ""
        try:
            return zlib.decompress(blob).decode("utf-8", errors="ignore")
        except Exception:
            return ""

    def upsert_documents_batch(
        self,
        records: List[Tuple[str, int, int, str, str]],
        ocr_engine: str = "",
        asr_engine: str = "",
    ) -> None:
        """Saves (path, mtime_ns, file_size, content_hash, raw_text) records.
        `ocr_engine` and `asr_engine` name the backends that read them ("" = not read yet).
        """
        if not records:
            return

        now = time.time()
        rows = [
            (path, mtime_ns, file_size, content_hash, self._compress(raw_text), now, ocr_engine, asr_engine)
            for path, mtime_ns, file_size, content_hash, raw_text in records
        ]

        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO documents
                    (path, mtime_ns, file_size, content_hash, compressed_text, updated_at,
                     ocr_engine, asr_engine)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    mtime_ns = excluded.mtime_ns,
                    file_size = excluded.file_size,
                    content_hash = excluded.content_hash,
                    compressed_text = excluded.compressed_text,
                    updated_at = excluded.updated_at,
                    ocr_engine = excluded.ocr_engine,
                    asr_engine = excluded.asr_engine
                """,
                rows,
            )

    def remove_documents(self, paths: List[str]) -> None:
        """Removes deleted file paths from the catalog."""
        if not paths:
            return
        with self._connect() as conn:
            conn.executemany(
                "DELETE FROM documents WHERE path = ?", [(p,) for p in paths]
            )

    def get_document_text(self, path: str) -> str:
        """Decompresses and returns the stored plain text for a document."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT compressed_text FROM documents WHERE path = ?", (path,)
            ).fetchone()
        return self._decompress(row[0]) if row else ""

    def iter_all_documents(self) -> Iterable[Tuple[str, int, int, str]]:
        """Yields (path, mtime_ns, file_size, plain_text) for all stored documents."""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT path, mtime_ns, file_size, compressed_text FROM documents"
            )
            for path, mtime_ns, file_size, blob in cursor:
                yield path, mtime_ns, file_size, self._decompress(blob)

    def iter_documents_updated_after(
        self, timestamp: float
    ) -> Iterable[Tuple[str, int, int, str]]:
        """Like `iter_all_documents`, limited to rows written after `timestamp`."""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT path, mtime_ns, file_size, compressed_text FROM documents "
                "WHERE updated_at > ?",
                (timestamp,),
            )
            for path, mtime_ns, file_size, blob in cursor.fetchall():
                yield path, mtime_ns, file_size, self._decompress(blob)

    def get_ocr_engines(self) -> Dict[str, str]:
        """Returns {path: ocr_engine} for every cataloged file."""
        with self._connect() as conn:
            return dict(conn.execute("SELECT path, ocr_engine FROM documents").fetchall())

    def get_asr_engines(self) -> Dict[str, str]:
        """Returns {path: asr_engine} for every cataloged file."""
        with self._connect() as conn:
            return dict(conn.execute("SELECT path, asr_engine FROM documents").fetchall())

    def get_transcript(self, content_hash: str, asr_engine: str) -> Optional[str]:
        """Returns the stored transcript, or None if this content was never transcribed.
        An empty string means it was transcribed and holds no speech.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT compressed_text FROM transcripts WHERE content_hash = ? AND asr_engine = ?",
                (content_hash, asr_engine),
            ).fetchone()
        return self._decompress(row[0]) if row else None

    def save_transcript(self, content_hash: str, asr_engine: str, text: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO transcripts (content_hash, asr_engine, compressed_text) "
                "VALUES (?, ?, ?)",
                (content_hash, asr_engine, self._compress(text)),
            )

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

        clean_text = re.sub(r"\s+", " ", text).strip()
        if not clean_text:
            return ""

        lower_text = clean_text.lower()
        candidate_positions: List[int] = []
        clean_words = [w.lower().strip() for w in matched_words if w and w.strip()]

        for w in clean_words:
            idx = lower_text.find(w)
            if idx != -1:
                candidate_positions.append(idx)

        if not candidate_positions:
            return (
                clean_text[:max_length] + "..."
                if len(clean_text) > max_length
                else clean_text
            )

        # Pick the candidate position whose window covers the most matched query words
        best_idx = min(candidate_positions)
        best_coverage = -1
        for pos in sorted(candidate_positions):
            w_start = max(0, pos - max_length // 3)
            w_end = min(len(lower_text), w_start + max_length)
            window_slice = lower_text[w_start:w_end]
            coverage = sum(1 for w in clean_words if w in window_slice)
            if coverage > best_coverage:
                best_coverage = coverage
                best_idx = pos

        half_window = max_length // 2
        start = max(0, best_idx - half_window)
        end = min(len(clean_text), start + max_length)

        if start > 0:
            space_idx = clean_text.find(" ", start)
            if 0 <= space_idx < best_idx:
                start = space_idx + 1
                end = min(len(clean_text), start + max_length)

        snippet = clean_text[start:end].strip()
        prefix = "..." if start > 0 else ""
        suffix = "..." if end < len(clean_text) else ""
        formatted = f"{prefix}{snippet}{suffix}"

        # Prefix the nearest preceding location marker ([02:30], [Page 3], [Sheet: X], ...)
        marker_pattern = re.compile(
            r"\[(?:\d{1,2}:\d{2}(?::\d{2})?|Page\s+\d+|Slide\s+\d+|Sheet:\s*[^\]]+|Chapter\s+\d+|OCR)\]"
        )
        if start > 0 and not marker_pattern.search(snippet[:25]):
            preceding_markers = list(marker_pattern.finditer(clean_text[: start + 15]))
            if preceding_markers:
                nearest_marker = preceding_markers[-1].group(0)
                if nearest_marker not in formatted:
                    formatted = f"{nearest_marker} {formatted}"

        return formatted

