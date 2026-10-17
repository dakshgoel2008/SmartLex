import concurrent.futures
import os
from collections import Counter
from typing import List, Optional, Tuple

from smartlex.core.catalog import CatalogDB
from smartlex.core.config import load_config
from smartlex.core.keyword_extraction import extract_keywords
from smartlex.core.logger import setup_logger
from smartlex.core.native_engine import SmartLexEngine
from smartlex.core.text_extraction import extract_text_from_file

logger = setup_logger(__name__)


def extract_keywords_from_file(file_path: str) -> List[str]:
    """Extracts normalized keywords from a supported file."""
    text = extract_text_from_file(file_path)
    return extract_keywords(text)


def _extract_file_record(path: str) -> Optional[Tuple[str, int, int, str, str]]:
    """
    Worker function that extracts full text + metadata for a single file.
    Returns (path, mtime_ns, file_size, content_hash, raw_text) or None if missing.
    """
    if not os.path.exists(path):
        return None

    stat_info = CatalogDB.stat_file(path)
    if stat_info is None:
        return None
    mtime_ns, file_size = stat_info

    text = extract_text_from_file(path)
    if not text and not os.path.basename(path):
        return None

    content_hash = CatalogDB.compute_file_hash(path)
    return (path, mtime_ns, file_size, content_hash, text or "")


def process_batch(batch_file: str) -> dict:
    """Backwards-compatible batch processor returning {path: [keywords]}."""
    result = {}
    if not os.path.exists(batch_file):
        logger.error(f"Batch file not found: {batch_file}")
        return result

    with open(batch_file, "r", encoding="utf-8") as f:
        paths = [line.strip() for line in f if line.strip()]

    for path in paths:
        if os.path.exists(path):
            kws = extract_keywords_from_file(path)
            if kws:
                result[path] = kws
    return result


def refine_keywords(data: dict, top_n: int) -> dict:
    """Backwards-compatible keyword frequency refiner."""
    for k, v in data.items():
        c = Counter(v)
        top_values = [w for w, _ in c.most_common(top_n)]
        data[k] = list(set(top_values))
    return data


def process_all_batches(
    batch_files: List[str],
    top_n: int = 150,
    progress_callback=None,
    engine: Optional[SmartLexEngine] = None,
    on_tier_ready=None,
) -> SmartLexEngine:
    """
    Phase-2 Progressive & Incremental Index Builder:
    1. Collects all scanned file paths from `batch_files`.
    2. Runs `detect_moves_and_plan_sync()` to detect:
       - Deleted files (removed in O(1) via tombstones)
       - Moved / Renamed files (updated in <1ms via content_hash without re-parsing!)
       - Unchanged files (skipped immediately!)
       - New / Modified files (queued for Tier-0 + Tier-1 progressive indexing)
    3. Tier 0 (Instant Filename Index): Immediately indexes all new filenames in C++
       so 100% of files are searchable by name/extension in <1 second!
    4. Tier 1 (Streaming Content Micro-Batches): Extracts document contents in
       micro-batches of 32 files, updating the live thread-safe C++ index as it goes.
    """
    cfg = load_config()
    index_file = cfg.get("INDEX_FILE", "index.slex")
    catalog_file = cfg.get("CATALOG_FILE", "smartlex_catalog.db")

    if engine is None:
        engine = SmartLexEngine(index_file=index_file, catalog_file=catalog_file)
        engine.load(index_file)

    all_paths: List[str] = []
    for bf in batch_files:
        if os.path.exists(bf):
            with open(bf, "r", encoding="utf-8") as f:
                all_paths.extend(line.strip() for line in f if line.strip())

    # Step 1: Detect moves/renames, deletions, unchanged, and new/modified files
    to_index, unchanged, deleted, moved = engine.catalog.detect_moves_and_plan_sync(
        all_paths
    )

    if deleted:
        engine.remove_documents(deleted)
        logger.info(f"Removed {len(deleted)} deleted file(s) from index.")

    if moved:
        for old_path, new_path, new_mtime_ns, file_size in moved:
            engine.rename_or_move_document(old_path, new_path, new_mtime_ns, file_size)
        logger.info(
            f"Updated {len(moved)} moved/renamed file(s) in <1ms without re-extraction!"
        )

    # Ensure unchanged files from catalog are loaded in the C++ index if starting cold
    if len(engine) == 0 and unchanged:
        for path, mtime_ns, file_size, text in engine.catalog.iter_all_documents():
            if os.path.exists(path):
                engine.native_index.add_document(path, text, mtime_ns, file_size)

    if not to_index:
        logger.info(
            f"Index up-to-date ({len(unchanged)} unchanged, {len(moved)} moved, {len(deleted)} deleted)."
        )
        if progress_callback:
            progress_callback(f"Index up-to-date ({len(engine)} files ready)")
        if on_tier_ready:
            on_tier_ready(engine)
        return engine

    # Step 2: Tier 0 — Instant Filename & Path Index (<1s)
    tier0_added = engine.index_filenames_tier0(to_index)
    if tier0_added > 0:
        logger.info(
            f"Tier-0 complete: {tier0_added} file(s) immediately searchable by filename/extension!"
        )
        if progress_callback:
            progress_callback(
                f"Tier-0 ready: {len(engine)} files searchable by name! Extracting contents..."
            )
        if on_tier_ready:
            on_tier_ready(engine)

    # Step 3: Tier 1 — Streaming Micro-Batches for Content Extraction
    max_workers = min(cfg.get("NUM_PROCESSES", 8), os.cpu_count() or 4)
    micro_batch_size = 32
    total_to_extract = len(to_index)
    extracted_so_far = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        for start in range(0, total_to_extract, micro_batch_size):
            chunk = to_index[start : start + micro_batch_size]
            records: List[Tuple[str, int, int, str, str]] = []

            for record in executor.map(_extract_file_record, chunk):
                if record is not None:
                    records.append(record)

            if records:
                engine.add_documents_batch(records)

            extracted_so_far += len(chunk)
            if progress_callback:
                progress_callback(
                    f"Indexing contents: {extracted_so_far}/{total_to_extract} files "
                    f"({len(unchanged)} unchanged skipped)..."
                )
            if on_tier_ready:
                on_tier_ready(engine)

    return engine


def process_files_parallel(
    file_paths: List[str],
    engine: Optional[SmartLexEngine] = None,
) -> SmartLexEngine:
    """
    Directly indexes a list of file paths in parallel using `SmartLexEngine`.
    """
    cfg = load_config()
    if engine is None:
        engine = SmartLexEngine(
            index_file=cfg.get("INDEX_FILE", "index.slex"),
            catalog_file=cfg.get("CATALOG_FILE", "smartlex_catalog.db"),
        )

    max_workers = min(cfg.get("NUM_PROCESSES", 8), os.cpu_count() or 4)
    records: List[Tuple[str, int, int, str, str]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        for rec in executor.map(_extract_file_record, file_paths):
            if rec is not None:
                records.append(rec)

    if records:
        engine.add_documents_batch(records)

    return engine

