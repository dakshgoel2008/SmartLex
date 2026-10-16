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
    Returns (path, mtime_ns, file_size, content_hash, raw_text) or None if empty.
    """
    if not os.path.exists(path):
        return None

    stat_info = CatalogDB.stat_file(path)
    if stat_info is None:
        return None
    mtime_ns, file_size = stat_info

    text = extract_text_from_file(path)
    # Even if a document has minimal body text, its filename is still valuable to index
    if not text and not os.path.basename(path):
        return None

    content_hash = CatalogDB.compute_file_hash(path)
    return (path, mtime_ns, file_size, content_hash, text or "")


def process_batch(batch_file: str) -> dict:
    """
    Backwards-compatible batch processor returning {path: [keywords]}.
    """
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
) -> SmartLexEngine:
    """
    Incremental Phase-1 Index Builder:
    1. Collects all scanned file paths from `batch_files`.
    2. Queries SQLite `CatalogDB` to identify which files are new/modified,
       which are unchanged (skipping re-extraction!), and which were deleted.
    3. Extracts full text in parallel only for new/modified files.
    4. Builds the compressed C++20 Positional Inverted Index (`SmartLexEngine`)
       with 100% of tokens preserved (no 150-word truncation!).
    """
    cfg = load_config()
    index_file = cfg.get("INDEX_FILE", "index.slex")
    catalog_file = cfg.get("CATALOG_FILE", "smartlex_catalog.db")

    engine = SmartLexEngine(index_file=index_file, catalog_file=catalog_file)
    engine.load(index_file)

    all_paths: List[str] = []
    for bf in batch_files:
        if os.path.exists(bf):
            with open(bf, "r", encoding="utf-8") as f:
                all_paths.extend(line.strip() for line in f if line.strip())

    # Determine delta against SQLite catalog
    to_index, unchanged, deleted = engine.catalog.plan_incremental_sync(all_paths)

    if deleted:
        engine.remove_documents(deleted)
        logger.info(f"Removed {len(deleted)} deleted file(s) from index.")

    # Ensure unchanged files from catalog are loaded in the C++ index
    if len(engine) == 0 and unchanged:
        for path, mtime_ns, file_size, text in engine.catalog.iter_all_documents():
            if os.path.exists(path):
                engine.native_index.add_document(path, text, mtime_ns, file_size)

    if not to_index:
        logger.info(
            f"All {len(unchanged)} file(s) are already up-to-date. Zero re-indexing needed!"
        )
        if progress_callback:
            progress_callback(f"All {len(unchanged)} files up-to-date!")
        return engine

    logger.info(
        f"Incremental indexing: {len(to_index)} new/modified file(s), "
        f"{len(unchanged)} unchanged file(s) skipped."
    )
    if progress_callback:
        progress_callback(
            f"Extracting {len(to_index)} new/modified files ({len(unchanged)} unchanged skipped)..."
        )

    records: List[Tuple[str, int, int, str, str]] = []
    max_workers = min(cfg.get("NUM_PROCESSES", 8), os.cpu_count() or 4)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        for record in executor.map(_extract_file_record, to_index):
            if record is not None:
                records.append(record)

    if records:
        engine.add_documents_batch(records)

    return engine
