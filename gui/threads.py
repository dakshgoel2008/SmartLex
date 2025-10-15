import json
import os
import subprocess
from pathlib import Path

from PyQt5.QtCore import QThread, pyqtSignal

from core.config import load_config
from core.index_manager import generate_autocomplete, save_index
from core.logger import setup_logger
from core.processor import process_all_batches

logger = setup_logger(__name__)


class IndexingThread(QThread):
    """Background thread for indexing operations."""
    
    progress = pyqtSignal(str)
    finished_signal = pyqtSignal(dict, list)
    error_signal = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cfg = load_config()
        self._is_cancelled = False

    def cancel(self):
        """Request cancellation of the indexing process."""
        self._is_cancelled = True
        logger.info("Indexing cancellation requested")

    def run(self):
        """Execute the indexing process."""
        try:
            if self._is_cancelled:
                return

            self._emit_progress("Starting indexing process...")
            
            # Step 1: Run batch script
            if not self._run_batch_script():
                return

            if self._is_cancelled:
                self._emit_progress("Indexing cancelled")
                return

            # Step 2: Collect batch files
            batch_files = self._collect_batch_files()
            if not batch_files:
                self._emit_error("No batch files found after script execution")
                return

            if self._is_cancelled:
                self._emit_progress("Indexing cancelled")
                return

            # Step 3: Process files
            self._emit_progress(f"Processing {len(batch_files)} batch(es) in parallel...")
            D = process_all_batches(batch_files, self.cfg["TOP_KEYWORDS"])

            if self._is_cancelled:
                self._emit_progress("Indexing cancelled")
                return

            if not D:
                self._emit_error("No data indexed. Check if PDF files exist in specified directories.")
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
            logger.info(f"Indexing completed: {len(D)} files indexed, {len(words)} autocomplete words")

        except Exception as e:
            error_msg = f"Indexing error: {str(e)}"
            logger.error(error_msg, exc_info=True)
            self._emit_error(error_msg)

    def _run_batch_script(self):
        """Run the appropriate batch script for the OS."""
        self._emit_progress("Running batch script to collect file paths...")
        
        try:
            if os.name == "nt":  # Windows
                script_path = Path("scripts/pdf_search.bat")
                if not script_path.exists():
                    self._emit_error(f"Batch script not found: {script_path}")
                    return False
                
                result = subprocess.run(
                    [str(script_path)],
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=300  # 5 minute timeout
                )
            else:  # Unix-like (Linux, macOS)
                script_path = Path("scripts/pdf_search.sh")
                if not script_path.exists():
                    self._emit_error(f"Shell script not found: {script_path}")
                    return False
                
                result = subprocess.run(
                    ["bash", str(script_path)],
                    capture_output=True,
                    text=True,
                    timeout=300
                )

            if result.returncode != 0:
                logger.warning(f"Batch script exited with code {result.returncode}")
                if result.stderr:
                    logger.warning(f"Script stderr: {result.stderr}")
            
            self._emit_progress("✓ Batch script completed")
            return True

        except subprocess.TimeoutExpired:
            self._emit_error("Batch script timed out after 5 minutes")
            return False
        except Exception as e:
            self._emit_error(f"Error running batch script: {str(e)}")
            return False

    def _collect_batch_files(self):
        """Collect all valid batch files."""
        self._emit_progress("Collecting batch files...")
        
        index_folder = Path(self.cfg["INDEX_FOLDER"])
        if not index_folder.exists():
            logger.error(f"Index folder not found: {index_folder}")
            return []

        batch_files = []
        for i in range(1, self.cfg["NUM_PROCESSES"] + 1):
            batch_file = index_folder / f"pdf_part_{i}.txt"
            if batch_file.exists():
                file_size = batch_file.stat().st_size
                if file_size > 0:
                    batch_files.append(str(batch_file))
                    logger.debug(f"Found batch file: {batch_file} ({file_size} bytes)")
                else:
                    logger.warning(f"Batch file is empty: {batch_file}")

        if batch_files:
            self._emit_progress(f"✓ Found {len(batch_files)} valid batch file(s)")
        else:
            logger.error(f"No batch files found in {index_folder}")

        return batch_files

    def _emit_progress(self, message):
        """Emit progress message if not cancelled."""
        if not self._is_cancelled:
            self.progress.emit(message)

    def _emit_error(self, message):
        """Emit error message."""
        logger.error(message)
        self.error_signal.emit(message)
        self.progress.emit(f"✗ ERROR: {message}")