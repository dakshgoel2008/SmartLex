import math
import os
import string
import time
from pathlib import Path
from typing import List

from smartlex.core.logger import setup_logger
from smartlex.core.native_engine import scan_directories

logger = setup_logger(__name__)

EXCLUDE_DIRS = {
    "windows",
    "program files",
    "program files (x86)",
    "programdata",
    "appdata",
    "system volume information",
    "$recycle.bin",
    "recovery",
    "proc",
    "sys",
    "dev",
    "run",
    "snap",
    "lost+found",
    "timeshift",
    "library",
    "node_modules",
    ".git",
    ".hg",
    ".svn",
    "venv",
    ".venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".tox",
    ".cache",
    ".npm",
    ".cargo",
    ".rustup",
    ".dotnet",
    ".gemini",
    "site-packages",
    "dist-packages",
    "build",
    "dist",
}


def get_available_drives() -> List[str]:
    drives = []
    if os.name == "nt":
        for letter in string.ascii_uppercase:
            drive = f"{letter}:\\"
            if os.path.exists(drive):
                drives.append(drive)
    else:
        home = os.path.expanduser("~")
        if os.path.exists(home):
            drives.append(home)
        else:
            drives.append("/")
    return drives


def scan_dir_parallel(root_path: str, extensions: List[str], progress_callback=None) -> List[str]:
    """
    Scans `root_path` using the C++20 multi-threaded `FileScanner`.
    Returns file paths sorted by priority (Desktop/Documents/Downloads first,
    then newest modification time first).
    """
    if progress_callback:
        progress_callback(f"Scanning {root_path} with C++20 parallel scanner...")

    records = scan_directories([str(root_path)], list(extensions), 8)
    paths = [rec[0] for rec in records]

    if progress_callback:
        progress_callback(f"Scanned {root_path}: found {len(paths)} files.")
    return paths


def scan_all_drives(extensions, num_processes, output_folder, progress_callback=None):
    drives = get_available_drives()
    all_found_files = []

    for drive in drives:
        if progress_callback:
            progress_callback(f"Starting C++20 scan on {drive}")

        start_time = time.time()
        files = scan_dir_parallel(drive, extensions, progress_callback)
        all_found_files.extend(files)

        logger.info(
            f"Scan completed on {drive} in {time.time() - start_time:.2f}s. Found {len(files)} files."
        )

    total_files = len(all_found_files)
    if progress_callback:
        progress_callback(f"Scan complete. Found {total_files} total files.")

    out_dir = Path(output_folder)
    out_dir.mkdir(parents=True, exist_ok=True)

    for old_file in out_dir.glob("pdf_part_*.txt"):
        try:
            old_file.unlink()
        except Exception as e:
            logger.warning(f"Could not delete old batch file {old_file}: {e}")

    if total_files == 0:
        return []

    batch_size = max(1, math.ceil(total_files / max(1, num_processes)))
    batch_files = []

    for i in range(num_processes):
        start_idx = i * batch_size
        end_idx = min((i + 1) * batch_size, total_files)

        batch_slice = all_found_files[start_idx:end_idx]
        if not batch_slice:
            break

        batch_file = out_dir / f"pdf_part_{i+1}.txt"
        with open(batch_file, "w", encoding="utf-8") as f:
            for filepath in batch_slice:
                f.write(f"{filepath}\n")

        batch_files.append(str(batch_file))

    return batch_files
