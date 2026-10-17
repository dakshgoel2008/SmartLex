import json
from pathlib import Path

from PyQt5.QtCore import QThread, pyqtSignal

from smartlex.core.config import load_config
from smartlex.core.index_manager import generate_autocomplete, save_index
from smartlex.core.logger import setup_logger
from smartlex.core.processor import process_all_batches
from smartlex.core.scanner import scan_all_drives

logger = setup_logger(__name__)


class IndexingThread(QThread):
    progress = pyqtSignal(str)
    tier_ready_signal = pyqtSignal(object, list)
    finished_signal = pyqtSignal(object, list)
    error_signal = pyqtSignal(str)

    def __init__(self, parent=None, engine=None):
        super().__init__(parent)
        self.cfg = load_config()
        self.engine = engine
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True
        logger.info("Indexing cancellation requested")

    def _on_tier_ready(self, live_engine):
        if not self._is_cancelled:
            words = generate_autocomplete(live_engine, self.cfg["AUTOCOMPLETE_WORDS"])
            self.tier_ready_signal.emit(live_engine, words)

    def run(self):
        try:
            if self._is_cancelled:
                return

            self._emit_progress("Scanning drives with C++20 parallel scanner...")
            batch_files = scan_all_drives(
                extensions=self.cfg["SUPPORTED_FORMATS"],
                num_processes=self.cfg["NUM_PROCESSES"],
                output_folder=self.cfg["INDEX_FOLDER"],
                progress_callback=self._emit_progress,
            )

            if self._is_cancelled:
                self._emit_progress("Indexing cancelled")
                return

            if not batch_files:
                self._emit_error("No documents found on the system.")
                return

            # Progressive Tier-0 (Instant Filenames) -> Tier-1 (Content Micro-Batches)
            engine = process_all_batches(
                batch_files,
                self.cfg["TOP_KEYWORDS"],
                progress_callback=self._emit_progress,
                engine=self.engine,
                on_tier_ready=self._on_tier_ready,
                should_cancel=lambda: self._is_cancelled,
            )

            if self._is_cancelled:
                self._emit_progress("Indexing cancelled")
                return

            if not engine:
                self._emit_error(
                    "No data indexed. Check if supported files exist in specified directories."
                )
                return

            # Save compressed binary index (.slex)
            self._emit_progress("Saving compressed binary index to disk...")
            index_target = self.cfg.get("INDEX_FILE", self.cfg["OUTPUT_FILE"])
            save_index(engine, index_target)

            if self._is_cancelled:
                self._emit_progress("Indexing cancelled")
                return

            # Generate and save autocomplete vocabulary
            words = generate_autocomplete(engine, self.cfg["AUTOCOMPLETE_WORDS"])
            autocomplete_path = Path(self.cfg["AUTOCOMPLETE_FILE"])
            autocomplete_path.parent.mkdir(parents=True, exist_ok=True)

            with open(autocomplete_path, "w", encoding="utf-8") as f:
                json.dump(words, f, indent=2)

            self._emit_progress(
                f"Ready — {len(engine)} files indexed & live watcher active"
            )
            self.finished_signal.emit(engine, words)
            logger.info(
                f"Progressive indexing completed: {len(engine)} files indexed, {len(words)} autocomplete words"
            )

        except Exception as e:
            error_msg = f"Indexing error: {str(e)}"
            logger.error(error_msg, exc_info=True)
            self._emit_error(error_msg)

    def _emit_progress(self, message):
        if not self._is_cancelled:
            self.progress.emit(message)

    def _emit_error(self, message):
        logger.error(message)
        self.error_signal.emit(message)
        self.progress.emit(f"ERROR: {message}")
