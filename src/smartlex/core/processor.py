import concurrent.futures
import os
from collections import Counter
from typing import Callable, Dict, List, Optional, Tuple

from smartlex.core.catalog import CatalogDB
from smartlex.core.config import load_config
from smartlex.core.keyword_extraction import extract_keywords
from smartlex.core.logger import setup_logger
from smartlex.core.media_extractors import (
    combine_image_text,
    get_ocr_engine_id,
    read_image_metadata,
    run_image_ocr,
)
from smartlex.core.native_engine import SmartLexEngine
from smartlex.core.text_extraction import extract_text_from_file, is_image_file

logger = setup_logger(__name__)

Record = Tuple[str, int, int, str, str]  # (path, mtime_ns, file_size, content_hash, text)

CONTENT_BATCH_SIZE = 32
# OCR takes seconds per image, so results are published in small batches to make
# them searchable soon after each image is read.
OCR_BATCH_SIZE = 4


def extract_keywords_from_file(file_path: str) -> List[str]:
    """Extracts normalized keywords from a supported file."""
    text = extract_text_from_file(file_path)
    return extract_keywords(text)


def _extract_file_record(path: str, include_ocr: bool = True) -> Optional[Record]:
    """Extracts one file. Returns None if it is gone; on failure returns empty text
    so the file stays searchable by name and the run continues.
    """
    stat_info = CatalogDB.stat_file(path)
    if stat_info is None:
        return None
    mtime_ns, file_size = stat_info

    try:
        text = extract_text_from_file(path, include_ocr=include_ocr)
    except Exception as e:
        logger.error(f"Could not extract text from {path}: {e}")
        text = ""

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


def _read_paths_from_batch_files(batch_files: List[str]) -> List[str]:
    paths: List[str] = []
    for batch_file in batch_files:
        if os.path.exists(batch_file):
            with open(batch_file, "r", encoding="utf-8") as f:
                paths.extend(line.strip() for line in f if line.strip())
    return paths


def _rebuild_index_from_catalog(engine: SmartLexEngine) -> None:
    for path, mtime_ns, file_size, text in engine.catalog.iter_all_documents():
        if os.path.exists(path):
            engine.native_index.add_document(path, text, mtime_ns, file_size)


def _ocr_image_record(path: str, ocr_text_by_hash: Dict[str, str]) -> Optional[Record]:
    """Builds the full record (metadata + OCR text) for one image."""
    stat_info = CatalogDB.stat_file(path)
    if stat_info is None:
        return None
    mtime_ns, file_size = stat_info
    content_hash = CatalogDB.compute_file_hash(path)

    # Identical copies of a picture (same bytes) are read only once.
    if content_hash and content_hash in ocr_text_by_hash:
        ocr_text = ocr_text_by_hash[content_hash]
    else:
        ocr_text = run_image_ocr(path)
        if content_hash:
            ocr_text_by_hash[content_hash] = ocr_text

    text = combine_image_text(read_image_metadata(path), ocr_text)
    return (path, mtime_ns, file_size, content_hash, text)


def run_ocr_pass(
    engine: SmartLexEngine,
    paths: List[str],
    progress_callback: Optional[Callable[[str], None]] = None,
    on_tier_ready: Optional[Callable[[SmartLexEngine], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> int:
    """OCRs every image in `paths` not yet read by the current engine. Returns the count."""
    ocr_engine = get_ocr_engine_id()
    if not ocr_engine:
        return 0

    stored_engines = engine.catalog.get_ocr_engines()
    pending = [
        path
        for path in paths
        if is_image_file(path) and path in stored_engines and stored_engines[path] != ocr_engine
    ]
    if not pending:
        return 0

    logger.info(f"Reading text in {len(pending)} image(s) with {ocr_engine}.")
    if progress_callback:
        progress_callback(f"Reading text in images: 0/{len(pending)} images...")
    ocr_text_by_hash: Dict[str, str] = {}
    batch: List[Record] = []
    processed = 0

    for path in pending:
        if should_cancel is not None and should_cancel():
            break

        record = _ocr_image_record(path, ocr_text_by_hash)
        if get_ocr_engine_id() != ocr_engine:
            # The engine failed to start or was swapped. Recording this image as read
            # would stop it from ever being retried, so leave the rest for the next sync.
            logger.warning("OCR engine changed during indexing; remaining images are left for the next sync.")
            break
        if record is not None:
            batch.append(record)
        processed += 1

        if len(batch) >= OCR_BATCH_SIZE or processed == len(pending):
            engine.add_documents_batch(batch, ocr_engine=ocr_engine)
            batch = []
            if progress_callback:
                progress_callback(f"Reading text in images: {processed}/{len(pending)} images...")
            if on_tier_ready:
                on_tier_ready(engine)

    # Keep the images finished before a cancel request.
    if batch:
        engine.add_documents_batch(batch, ocr_engine=ocr_engine)
    return processed


def process_all_batches(
    batch_files: List[str],
    top_n: int = 150,
    progress_callback=None,
    engine: Optional[SmartLexEngine] = None,
    on_tier_ready=None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> SmartLexEngine:
    """Syncs the index with the listed files: names first, then contents, then image OCR.
    `should_cancel` is polled between batches.
    """
    cfg = load_config()
    index_file = cfg.get("INDEX_FILE", "index.slex")
    catalog_file = cfg.get("CATALOG_FILE", "smartlex_catalog.db")

    if engine is None:
        engine = SmartLexEngine(index_file=index_file, catalog_file=catalog_file)
        engine.load(index_file)

    def cancelled() -> bool:
        return should_cancel is not None and should_cancel()

    all_paths = _read_paths_from_batch_files(batch_files)

    # No usable index file: rebuild from the catalog. Must run before moves and
    # deletions, which would make the index non-empty and skip the rebuild.
    if len(engine) == 0:
        _rebuild_index_from_catalog(engine)

    to_index, unchanged, deleted, moved = engine.catalog.detect_moves_and_plan_sync(all_paths)

    if deleted:
        engine.remove_documents(deleted)
        logger.info(f"Removed {len(deleted)} deleted file(s) from index.")

    for old_path, new_path, new_mtime_ns, file_size in moved:
        engine.rename_or_move_document(old_path, new_path, new_mtime_ns, file_size)
    if moved:
        logger.info(f"Updated {len(moved)} moved/renamed file(s) without re-extraction.")

    if to_index:
        tier0_added = engine.index_filenames_tier0(to_index)
        if tier0_added > 0:
            logger.info(f"Tier-0: {tier0_added} file(s) searchable by file name.")
            if progress_callback:
                progress_callback(
                    f"Tier-0 ready: {len(engine)} files searchable by name! Extracting contents..."
                )
            if on_tier_ready:
                on_tier_ready(engine)

        max_workers = min(cfg.get("NUM_PROCESSES", 8), os.cpu_count() or 4)
        total = len(to_index)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            for start in range(0, total, CONTENT_BATCH_SIZE):
                if cancelled():
                    return engine
                chunk = to_index[start : start + CONTENT_BATCH_SIZE]
                records = [
                    record
                    for record in executor.map(
                        lambda path: _extract_file_record(path, include_ocr=False), chunk
                    )
                    if record is not None
                ]
                engine.add_documents_batch(records)

                done = min(start + CONTENT_BATCH_SIZE, total)
                if progress_callback:
                    progress_callback(
                        f"Indexing contents: {done}/{total} files "
                        f"({len(unchanged)} unchanged skipped)..."
                    )
                if on_tier_ready:
                    on_tier_ready(engine)
    else:
        logger.info(
            f"Index up-to-date ({len(unchanged)} unchanged, {len(moved)} moved, {len(deleted)} deleted)."
        )
        if progress_callback:
            progress_callback(f"Index up-to-date ({len(engine)} files ready)")
        if on_tier_ready:
            on_tier_ready(engine)

    if not cancelled():
        deleted_set = set(deleted)
        current_paths = [path for path in all_paths if path not in deleted_set]
        run_ocr_pass(engine, current_paths, progress_callback, on_tier_ready, should_cancel)

    return engine


def process_files_parallel(
    file_paths: List[str],
    engine: Optional[SmartLexEngine] = None,
) -> SmartLexEngine:
    """
    Indexes a list of file paths in parallel, including OCR of images, and
    returns once everything is searchable.
    """
    cfg = load_config()
    if engine is None:
        engine = SmartLexEngine(
            index_file=cfg.get("INDEX_FILE", "index.slex"),
            catalog_file=cfg.get("CATALOG_FILE", "smartlex_catalog.db"),
        )

    max_workers = min(cfg.get("NUM_PROCESSES", 8), os.cpu_count() or 4)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        records = [rec for rec in executor.map(_extract_file_record, file_paths) if rec is not None]

    engine.add_documents_batch(records, ocr_engine=get_ocr_engine_id())
    return engine
