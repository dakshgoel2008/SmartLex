import math
import os
import string
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from smartlex.core.logger import setup_logger

logger = setup_logger(__name__)

EXCLUDE_DIRS = {
    "windows",
    "program files",
    "program files (x86)",
    "appdata",
    "node_modules",
    ".git",
    "venv",
    ".venv",
    "__pycache__",
    "system volume information",
    "$recycle.bin",
}


def get_available_drives():
    drives = []
    if os.name == "nt":
        for letter in string.ascii_uppercase:
            drive = f"{letter}:\\"
            if os.path.exists(drive):
                drives.append(drive)
    else:
        # for Unix type systems
        drives.append("/")
    return drives


def scan_dir_parallel(root_path, extensions, progress_callback=None):
    found_files = []

    def scan_task(path):
        local_found = []
        subdirs = []
        try:
            with os.scandir(path) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name.lower() not in EXCLUDE_DIRS:
                                subdirs.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            if any(
                                entry.name.lower().endswith(ext) for ext in extensions
                            ):
                                local_found.append(entry.path)
                    except Exception:
                        pass
        except Exception:
            # Handle permission errors or missing directories silently
            pass
        return local_found, subdirs

    # Use a thread pool to traverse directories concurrently
    # Max workers is 32 to maximize I/O throughput
    pool = ThreadPoolExecutor(max_workers=32)
    futures = set([pool.submit(scan_task, root_path)])

    last_callback_time = time.time()
    files_since_last = 0

    while futures:
        done = []
        for f in list(futures):
            if f.done():
                done.append(f)

        if not done:
            time.sleep(0.01)
            continue

        for f in done:
            futures.remove(f)
            files, dirs = f.result()
            found_files.extend(files)
            files_since_last += len(files)
            for d in dirs:
                futures.add(pool.submit(scan_task, d))

        # Call progress callback periodically (e.g. every 0.5s) if there's activity
        if progress_callback and (time.time() - last_callback_time > 0.5):
            if files_since_last > 0:
                progress_callback(
                    f"Scanning {root_path}... Found {len(found_files)} files so far"
                )
                files_since_last = 0
                last_callback_time = time.time()

    pool.shutdown()
    return found_files


def scan_all_drives(extensions, num_processes, output_folder, progress_callback=None):
    drives = get_available_drives()
    all_found_files = []

    for drive in drives:
        if progress_callback:
            progress_callback(f"Starting scan on drive {drive}")

        start_time = time.time()
        files = scan_dir_parallel(drive, extensions, progress_callback)
        all_found_files.extend(files)

        logger.info(
            f"Scan completed on {drive} in {time.time() - start_time:.2f}s. Found {len(files)} files."
        )

    total_files = len(all_found_files)
    if progress_callback:
        progress_callback(
            f"Scan complete. Found {total_files} total files across all drives."
        )

    # Ensure output folder exists
    out_dir = Path(output_folder)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Clear old batches
    for old_file in out_dir.glob("pdf_part_*.txt"):
        try:
            old_file.unlink()
        except Exception as e:
            logger.warning(f"Could not delete old batch file {old_file}: {e}")

    if total_files == 0:
        return []

    # Split files into batches
    batch_size = max(1, math.ceil(total_files / num_processes))
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
