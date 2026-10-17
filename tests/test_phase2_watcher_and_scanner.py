import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path

from smartlex.core.native_engine import SmartLexEngine, scan_directories
from smartlex.core.processor import process_all_batches
from smartlex.core.search_engine import search
from smartlex.core.watcher import RealTimeFileWatcher


class TestPhase2ZeroWaitAndWatcher(unittest.TestCase):

    def test_cpp20_parallel_scanner_priority_and_exclusions(self):
        """Scanner skips excluded folders and orders user folders and newer files first."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            docs_dir = root / "Documents"
            other_dir = root / "archive_data"
            git_dir = root / ".git"
            nm_dir = root / "node_modules"

            for d in (docs_dir, other_dir, git_dir, nm_dir):
                d.mkdir(parents=True, exist_ok=True)

            f_other = other_dir / "old_notes.txt"
            f_docs_old = docs_dir / "older_doc.txt"
            f_docs_new = docs_dir / "newest_doc.txt"
            f_ignored_git = git_dir / "config.txt"
            f_ignored_nm = nm_dir / "pkg.txt"

            for f in (f_other, f_docs_old, f_docs_new, f_ignored_git, f_ignored_nm):
                f.write_text("sample content", encoding="utf-8")

            # Set explicit modification times so newest_doc.txt > older_doc.txt
            os.utime(f_docs_old, (1000, 1000))
            os.utime(f_docs_new, (2000, 2000))
            os.utime(f_other, (3000, 3000))

            scanned = scan_directories([str(root)], [".txt"], num_threads=4)
            scanned_paths = [rec[0] for rec in scanned]

            # Excluded directories must not appear
            self.assertNotIn(str(f_ignored_git), scanned_paths)
            self.assertNotIn(str(f_ignored_nm), scanned_paths)
            self.assertEqual(len(scanned_paths), 3)

            # High-priority Documents folder (tier 0) must come before archive_data (tier 1)
            self.assertEqual(scanned_paths[0], str(f_docs_new))
            self.assertEqual(scanned_paths[1], str(f_docs_old))
            self.assertEqual(scanned_paths[2], str(f_other))

    def test_tier0_instant_filename_indexing(self):
        """
        Verifies that Tier-0 filename indexing makes files immediately searchable
        by filename, path, and extension BEFORE body text extraction even starts.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = SmartLexEngine(
                index_file=os.path.join(tmpdir, "index.slex"),
                catalog_file=os.path.join(tmpdir, "catalog.db"),
            )
            paths = [
                "/home/user/Documents/quarterly_budget_2026.pdf",
                "/home/user/Downloads/neural_architecture_slides.docx",
            ]
            added = engine.index_filenames_tier0(paths)
            self.assertEqual(added, 2)

            # Immediately searchable by filename keywords and ext: filter!
            self.assertEqual(
                search("quarterly budget 2026", engine),
                ["/home/user/Documents/quarterly_budget_2026.pdf"],
            )
            self.assertEqual(
                search("neural ext:docx", engine),
                ["/home/user/Downloads/neural_architecture_slides.docx"],
            )

    def test_move_and_rename_detection_zero_reextraction(self):
        """
        Verifies that moving or renaming an indexed file is detected via
        (file_size, content_hash) and updated in <1ms without re-extraction.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            old_dir = root / "Downloads"
            new_dir = root / "Documents"
            old_dir.mkdir()
            new_dir.mkdir()

            old_file = old_dir / "draft_contract.txt"
            old_file.write_text(
                "Exclusive distribution agreement for quantum processors in 2026.",
                encoding="utf-8",
            )

            engine = SmartLexEngine(
                index_file=str(root / "index.slex"),
                catalog_file=str(root / "catalog.db"),
            )
            engine.add_or_update_document(str(old_file), old_file.read_text(encoding="utf-8"))

            # Move and rename the file on disk
            new_file = new_dir / "signed_contract.txt"
            shutil.move(str(old_file), str(new_file))

            # Plan sync: must detect the move and require 0 files to be re-extracted!
            to_index, unchanged, deleted, moved = engine.catalog.detect_moves_and_plan_sync(
                [str(new_file)]
            )
            self.assertEqual(to_index, [])
            self.assertEqual(deleted, [])
            self.assertEqual(len(moved), 1)
            self.assertEqual(moved[0][0], str(old_file))
            self.assertEqual(moved[0][1], str(new_file))

            # Apply the move
            old_p, new_p, new_mtime, fsize = moved[0]
            engine.rename_or_move_document(old_p, new_p, new_mtime, fsize)

            # Verify search by body content AND by new filename returns new_file
            self.assertEqual(search("quantum processors", engine), [str(new_file)])
            self.assertEqual(search("signed contract", engine), [str(new_file)])
            self.assertEqual(search("draft", engine), [])

    def test_realtime_watcher_create_modify_move_delete(self):
        """
        Verifies that RealTimeFileWatcher keeps SmartLexEngine in sync across
        live file creation, modification, move/rename, and deletion events.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            watch_dir = root / "workspace"
            watch_dir.mkdir()

            engine = SmartLexEngine(
                index_file=str(root / "index.slex"),
                catalog_file=str(root / "catalog.db"),
            )
            watcher = RealTimeFileWatcher(
                engine=engine,
                watch_roots=[str(watch_dir)],
                supported_extensions=[".txt", ".md"],
                debounce_seconds=0.01,
            )

            # 1. Create a new file
            f1 = watch_dir / "alpha.txt"
            f1.write_text("Project Apollo launch sequence initialized.", encoding="utf-8")
            watcher.notify_created_or_modified(str(f1))
            watcher.flush_ready_events(force=True)
            self.assertEqual(search("apollo launch", engine), [str(f1)])

            # 2. Modify the file
            f1.write_text("Project Artemis lunar landing telemetry.", encoding="utf-8")
            watcher.notify_created_or_modified(str(f1))
            watcher.flush_ready_events(force=True)
            self.assertEqual(search("artemis lunar", engine), [str(f1)])
            self.assertEqual(search("apollo", engine), [])

            # 3. Rename the file
            f2 = watch_dir / "artemis_final.txt"
            shutil.move(str(f1), str(f2))
            watcher.notify_moved(str(f1), str(f2))
            watcher.flush_ready_events(force=True)
            self.assertEqual(search("artemis lunar", engine), [str(f2)])

            # 4. Delete the file
            f2.unlink()
            watcher.notify_deleted(str(f2))
            watcher.flush_ready_events(force=True)
            self.assertEqual(search("artemis lunar", engine), [])
            self.assertEqual(len(engine), 0)

    def test_concurrent_multithreaded_search_and_indexing(self):
        """
        Stress-tests C++20 `std::shared_mutex` reader-writer lock by running
        concurrent background indexing and search threads simultaneously.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = SmartLexEngine(
                index_file=os.path.join(tmpdir, "index.slex"),
                catalog_file=os.path.join(tmpdir, "catalog.db"),
            )
            errors = []

            def writer_worker():
                try:
                    for i in range(150):
                        p = f"/concurrent/doc_{i}.txt"
                        engine.add_or_update_document(
                            p,
                            f"Concurrent systems programming in C++20 document {i}",
                            save_to_catalog=False,
                        )
                        if i % 10 == 0 and i > 0:
                            engine. native_index.rename_document(
                                f"/concurrent/doc_{i-1}.txt",
                                f"/concurrent/renamed_{i-1}.txt",
                            )
                except Exception as e:
                    errors.append(e)

            def reader_worker():
                try:
                    for _ in range(150):
                        _ = engine.search_paths("concurrent programming C++20")
                        _ = engine.suggest_autocomplete("con", 10)
                except Exception as e:
                    errors.append(e)

            threads = [
                threading.Thread(target=writer_worker),
                threading.Thread(target=reader_worker),
                threading.Thread(target=reader_worker),
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            self.assertEqual(errors, [])
            self.assertEqual(len(engine), 150)


if __name__ == "__main__":
    unittest.main()

