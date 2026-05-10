import json
import os
import subprocess
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
    finished_signal = pyqtSignal(dict, list)
    error_signal = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cfg = load_config()
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True
        logger.info("Indexing cancellation requested")

    def run(self):
        try:
            if self._is_cancelled:
                return

            self._emit_progress("Starting indexing process...")

            # Step 1 & 2: Scan all drives and create batch files
            self._emit_progress(
                "Scanning all system drives for documents. This may take a moment..."
            )
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

            # Step 3: Process files
            self._emit_progress(
                f"Processing {len(batch_files)} batch(es) in parallel..."
            )
            D = process_all_batches(batch_files, self.cfg["TOP_KEYWORDS"])

            if self._is_cancelled:
                self._emit_progress("Indexing cancelled")
                return

            if not D:
                self._emit_error(
                    "No data indexed. Check if PDF files exist in specified directories."
                )
                return

            # Step 4: Save index
            self._emit_progress("Saving index to disk...")
            save_index(D, self.cfg["OUTPUT_FILE"])
            self._emit_progress(f"Index saved with {len(D)} entries")

            if self._is_cancelled:
                self._emit_progress("Indexing cancelled")
                return

            # Step 5: Generate autocomplete
            self._emit_progress("Generating autocomplete data...")
            words = generate_autocomplete(D, self.cfg["AUTOCOMPLETE_WORDS"])

            autocomplete_path = Path(self.cfg["AUTOCOMPLETE_FILE"])
            autocomplete_path.parent.mkdir(parents=True, exist_ok=True)

            with open(autocomplete_path, "w", encoding="utf-8") as f:
                json.dump(words, f, indent=2)

            self._emit_progress(f"Autocomplete saved with {len(words)} words")

            # Finish
            self._emit_progress("✓ Indexing completed successfully!")
            self.finished_signal.emit(D, words)
            logger.info(
                f"Indexing completed: {len(D)} files indexed, {len(words)} autocomplete words"
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
        self.progress.emit(f"✗ ERROR: {message}")
