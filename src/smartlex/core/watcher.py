import os
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

from smartlex.core.logger import setup_logger
from smartlex.core.media_extractors import get_ocr_engine_id
from smartlex.core.native_engine import SmartLexEngine, scan_directories
from smartlex.core.text_extraction import extract_text_from_file, is_image_file

logger = setup_logger(__name__)

# Optional watchdog import (gracefully falls back to C++20 fast delta monitor if absent)
try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    HAS_WATCHDOG = True
except ImportError:
    FileSystemEventHandler = object
    Observer = None
    HAS_WATCHDOG = False


class RealTimeFileWatcher:
    """Keeps the index in sync with file creations, edits, moves and deletions, debouncing rapid saves."""

    def __init__(
        self,
        engine: SmartLexEngine,
        watch_roots: Iterable[str],
        supported_extensions: Iterable[str],
        debounce_seconds: float = 0.25,
        poll_interval: float = 1.0,
        on_index_updated: Optional[Callable[[SmartLexEngine], None]] = None,
    ):
        self.engine = engine
        self.watch_roots = [str(r) for r in watch_roots if os.path.exists(str(r))]
        self.supported_extensions: Tuple[str, ...] = tuple(
            ext.lower() if ext.startswith(".") else f".{ext.lower()}"
            for ext in supported_extensions
        )
        self.debounce_seconds = debounce_seconds
        self.poll_interval = poll_interval
        self.on_index_updated = on_index_updated

        self._lock = threading.Lock()
        # path -> timestamp of last event
        self._pending_upserts: Dict[str, float] = {}
        self._pending_deletes: Set[str] = set()
        # (old_path, new_path)
        self._pending_moves: List[Tuple[str, str]] = []

        self._stop_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None
        self._observer = None
        self._last_snapshot: Dict[str, Tuple[int, int]] = {}

    def is_supported_file(self, path: str) -> bool:
        name_lower = os.path.basename(path).lower()
        if not name_lower or name_lower.startswith(".") or name_lower.startswith("~$"):
            return False  # Ignore hidden and Office temporary lock files
        if (
            name_lower in ("autocomplete_words.json", "output.json", "search_engine.log", "cli_batch.txt")
            or (name_lower.startswith("pdf_part_") and name_lower.endswith(".txt"))
        ):
            return False  # Ignore SmartLex's own internal runtime artifacts
        if not name_lower.endswith(self.supported_extensions):
            return False

        # Ensure no directory component inside the watched root is a hidden or excluded directory
        from smartlex.core.scanner import EXCLUDE_DIRS

        norm_path = os.path.abspath(path)
        for root in self.watch_roots:
            norm_root = os.path.abspath(root)
            if norm_path.startswith(norm_root + os.sep):
                rel_dir = os.path.dirname(norm_path[len(norm_root) + 1 :])
                if rel_dir:
                    for part in Path(rel_dir).parts:
                        p_low = part.lower()
                        if p_low.startswith(".") or p_low in EXCLUDE_DIRS or p_low in ("logs", "backups", "all"):
                            return False
        return True

    # Event recording; called from watchdog threads.

    def notify_created_or_modified(self, path: str) -> None:
        if not self.is_supported_file(path):
            return
        with self._lock:
            self._pending_deletes.discard(path)
            self._pending_upserts[path] = time.monotonic()

    def notify_deleted(self, path: str) -> None:
        if not self.is_supported_file(path):
            return
        with self._lock:
            self._pending_upserts.pop(path, None)
            self._pending_deletes.add(path)

    def notify_moved(self, old_path: str, new_path: str) -> None:
        old_ok = self.is_supported_file(old_path)
        new_ok = self.is_supported_file(new_path)
        with self._lock:
            if old_ok and new_ok:
                self._pending_upserts.pop(old_path, None)
                self._pending_deletes.discard(old_path)
                self._pending_moves.append((old_path, new_path))
            elif old_ok and not new_ok:
                self.notify_deleted(old_path)
            elif not old_ok and new_ok:
                self.notify_created_or_modified(new_path)

    def flush_ready_events(self, force: bool = False) -> int:
        """
        Processes all debounced filesystem events that have settled.
        Returns the total number of index operations applied.
        """
        now = time.monotonic()
        with self._lock:
            moves = list(self._pending_moves)
            self._pending_moves.clear()

            deletes = list(self._pending_deletes)
            self._pending_deletes.clear()

            ready_upserts: List[str] = []
            for path, evt_time in list(self._pending_upserts.items()):
                if force or (now - evt_time) >= self.debounce_seconds:
                    ready_upserts.append(path)
                    del self._pending_upserts[path]

        ops_count = 0

        # 1. Moves / renames reuse the stored text
        for old_path, new_path in moves:
            stat_info = self.engine.catalog.stat_file(new_path)
            mtime_ns, fsize = stat_info if stat_info else (0, 0)
            if self.engine.rename_or_move_document(old_path, new_path, mtime_ns, fsize):
                ops_count += 1
                logger.info(f"[Watcher] Renamed/Moved in index: {old_path} -> {new_path}")

        # 2. Deletions
        if deletes:
            self.engine.remove_documents(deletes)
            ops_count += len(deletes)
            logger.info(f"[Watcher] Removed {len(deletes)} deleted file(s) from index.")

        # 3. Created / modified files
        for path in ready_upserts:
            try:
                if self._index_file(path):
                    ops_count += 1
                    logger.info(f"[Watcher] Indexed updated file: {path}")
            except Exception as e:
                logger.error(f"[Watcher] Could not index {path}: {e}")

        if ops_count > 0:
            self.engine.save()
            if self.on_index_updated:
                self.on_index_updated(self.engine)

        return ops_count

    def _index_file(self, path: str) -> bool:
        """Extracts and indexes one created or modified file. Returns False if skipped."""
        stat_info = self.engine.catalog.stat_file(path)
        if stat_info is None:
            return False
        mtime_ns, file_size = stat_info
        if self.engine.native_index.is_up_to_date(path, mtime_ns, file_size):
            return False

        ocr_engine = get_ocr_engine_id() if is_image_file(path) else ""
        # Transcription can take minutes, so it is left to the background speech pass.
        text = extract_text_from_file(path, include_asr=False)
        content_hash = self.engine.catalog.compute_file_hash(path)
        self.engine.add_or_update_document(
            path,
            text,
            mtime_ns=mtime_ns,
            file_size=file_size,
            content_hash=content_hash,
            save_to_catalog=True,
            ocr_engine=ocr_engine,
        )
        return True

    def scan_for_delta_events(self) -> int:
        """
        Uses the C++20 parallel `scan_directories` engine to detect any file
        creations, modifications, moves/renames, or deletions across `watch_roots`.
        """
        if not self.watch_roots:
            return 0

        scanned = scan_directories(self.watch_roots, list(self.supported_extensions), 4)
        current_paths = [rec[0] for rec in scanned]

        to_index, _unchanged, deleted, moved = self.engine.catalog.detect_moves_and_plan_sync(
            current_paths
        )

        for old_path, new_path, _mtime, _size in moved:
            self.notify_moved(old_path, new_path)

        for del_path in deleted:
            self.notify_deleted(del_path)

        for mod_path in to_index:
            self.notify_created_or_modified(mod_path)

        return self.flush_ready_events(force=True)

    def start(self) -> None:
        if self._worker_thread is not None and self._worker_thread.is_alive():
            return

        self._stop_event.clear()

        if HAS_WATCHDOG and self.watch_roots:
            watcher_self = self

            class _Handler(FileSystemEventHandler):
                def on_created(self, event):
                    if not event.is_directory:
                        watcher_self.notify_created_or_modified(event.src_path)

                def on_modified(self, event):
                    if not event.is_directory:
                        watcher_self.notify_created_or_modified(event.src_path)

                def on_deleted(self, event):
                    if not event.is_directory:
                        watcher_self.notify_deleted(event.src_path)

                def on_moved(self, event):
                    if not event.is_directory:
                        watcher_self.notify_moved(event.src_path, event.dest_path)

            try:
                self._observer = Observer()
                handler = _Handler()
                for root in self.watch_roots:
                    self._observer.schedule(handler, root, recursive=True)
                self._observer.start()
                logger.info(f"[Watcher] Started OS watchdog on {len(self.watch_roots)} root(s).")
            except Exception as e:
                logger.warning(f"[Watcher] Watchdog init failed ({e}); using C++20 delta monitor.")
                self._observer = None

        self._worker_thread = threading.Thread(
            target=self._background_loop, name="SmartLexWatcher", daemon=True
        )
        self._worker_thread.start()

    def _background_loop(self) -> None:
        last_delta_scan = time.monotonic()
        while not self._stop_event.is_set():
            try:
                self.flush_ready_events(force=False)
                # If OS watchdog is unavailable, run periodic C++20 delta scan
                if self._observer is None and (
                    time.monotonic() - last_delta_scan >= self.poll_interval
                ):
                    self.scan_for_delta_events()
                    last_delta_scan = time.monotonic()
            except Exception as e:
                logger.error(f"[Watcher] Background loop error: {e}")
            self._stop_event.wait(0.1)

    def stop(self) -> None:
        self._stop_event.set()
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=2.0)
            except Exception:
                pass
            self._observer = None
        if self._worker_thread is not None and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=2.0)
        self._worker_thread = None

