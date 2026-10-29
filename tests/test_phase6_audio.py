"""Audio search tests: the background speech pass, its cache, and the catalog columns it uses."""

import sqlite3
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from smartlex.core import processor
from smartlex.core.catalog import CatalogDB
from smartlex.core.media_extractors import register_asr_backend
from smartlex.core.native_engine import SmartLexEngine
from smartlex.core.processor import process_all_batches
from smartlex.core.watcher import RealTimeFileWatcher


def write_silent_wav(path: Path, seconds: int = 1) -> None:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 16000 * seconds)


class AudioTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.engine = SmartLexEngine(
            index_file=str(self.root / "index.slex"),
            catalog_file=str(self.root / "catalog.db"),
        )
        self.transcribed = []
        register_asr_backend(self.fake_asr)

    def tearDown(self):
        register_asr_backend(None)
        self.temp_dir.cleanup()

    def fake_asr(self, path: str) -> str:
        self.transcribed.append(path)
        return "[00:05] the quarterly budget was approved"

    def make_wav(self, name: str, seconds: int = 1) -> Path:
        path = self.root / f"{name}.wav"
        write_silent_wav(path, seconds)
        return path

    def make_wav_with_subtitles(self, name: str) -> Path:
        path = self.make_wav(name)
        (self.root / f"{name}.srt").write_text(
            "1\n00:00:02,000 --> 00:00:04,000\nwelcome to the planning meeting\n",
            encoding="utf-8",
        )
        return path

    def sync(self, paths, should_cancel=None) -> None:
        batch_file = self.root / "batch.txt"
        batch_file.write_text("".join(f"{p}\n" for p in paths), encoding="utf-8")
        process_all_batches([str(batch_file)], engine=self.engine, should_cancel=should_cancel)

    def search_paths(self, query: str):
        return [hit["path"] for hit in self.engine.search(query)]


class TestAsrPass(AudioTestCase):
    def test_sync_transcribes_audio_after_other_contents(self):
        notes = self.root / "notes.txt"
        notes.write_text("minutes of the harbour committee", encoding="utf-8")
        wav = self.make_wav("meeting")
        notes_found_during_transcription = []
        register_asr_backend(
            lambda path: notes_found_during_transcription.append(self.search_paths("harbour"))
            or self.fake_asr(path)
        )

        self.sync([notes, wav])

        self.assertEqual(notes_found_during_transcription, [[str(notes)]])
        hits = self.engine.search("quarterly budget")
        self.assertEqual([h["path"] for h in hits], [str(wav)])
        self.assertIn("[00:05]", hits[0]["snippet"])
        self.assertEqual(self.engine.catalog.get_asr_engines()[str(wav)], "custom")

    def test_unchanged_files_are_not_transcribed_again(self):
        wav = self.make_wav("meeting")
        self.sync([wav])
        self.sync([wav])
        self.assertEqual(self.transcribed, [str(wav)])

    def test_identical_copies_are_transcribed_once(self):
        first = self.make_wav("meeting")
        second = self.make_wav("meeting copy")

        self.sync([first, second])

        self.assertEqual(len(self.transcribed), 1)
        self.assertEqual(set(self.search_paths("quarterly budget")), {str(first), str(second)})

    def test_files_with_subtitles_are_not_transcribed(self):
        wav = self.make_wav_with_subtitles("meeting")

        self.sync([wav])

        self.assertEqual(self.transcribed, [])
        self.assertEqual(self.search_paths("planning meeting"), [str(wav)])
        self.assertEqual(self.engine.catalog.get_asr_engines(), {str(wav): "custom"})

    def test_smallest_files_are_transcribed_first(self):
        long_wav = self.make_wav("long", seconds=3)
        short_wav = self.make_wav("short", seconds=1)
        medium_wav = self.make_wav("medium", seconds=2)

        self.sync([long_wav, short_wav, medium_wav])

        self.assertEqual(self.transcribed, [str(short_wav), str(medium_wav), str(long_wav)])

    def test_cancelled_sync_leaves_remaining_files_for_the_next_one(self):
        first = self.make_wav("first", seconds=1)
        second = self.make_wav("second", seconds=2)

        self.sync([first, second], should_cancel=lambda: len(self.transcribed) >= 1)
        self.assertEqual(
            self.engine.catalog.get_asr_engines(), {str(first): "custom", str(second): ""}
        )

        self.sync([first, second])
        self.assertEqual(self.transcribed, [str(first), str(second)])

    def test_failed_transcription_is_retried_on_the_next_sync(self):
        wav = self.make_wav("meeting")

        def broken_asr(path):
            raise RuntimeError("decoder crashed")

        register_asr_backend(broken_asr)
        self.sync([wav])
        self.assertEqual(self.engine.catalog.get_asr_engines(), {str(wav): ""})

        register_asr_backend(self.fake_asr)
        self.sync([wav])
        self.assertEqual(self.transcribed, [str(wav)])
        self.assertEqual(self.search_paths("quarterly budget"), [str(wav)])

    def test_files_are_transcribed_once_a_speech_engine_is_installed(self):
        wav = self.make_wav("meeting")
        with mock.patch.object(processor, "get_asr_engine_id", return_value=""):
            self.sync([wav])
        self.assertEqual(self.transcribed, [])

        self.sync([wav])
        self.assertEqual(self.transcribed, [str(wav)])
        self.assertEqual(self.search_paths("quarterly budget"), [str(wav)])

    def test_watcher_indexes_audio_without_transcribing(self):
        wav = self.make_wav_with_subtitles("standup")
        watcher = RealTimeFileWatcher(self.engine, [str(self.root)], [".wav"])

        self.assertTrue(watcher._index_file(str(wav)))

        self.assertEqual(self.transcribed, [])
        self.assertEqual(self.search_paths("planning meeting"), [str(wav)])


class TestAsrCatalogStorage(AudioTestCase):
    def test_asr_engine_is_saved_per_document(self):
        self.engine.add_documents_batch(
            [("/a.wav", 1, 10, "hash-a", "hello")], asr_engine="whisper-small"
        )
        self.engine.add_or_update_document("/b.wav", "", 1, 10, content_hash="hash-b")

        self.assertEqual(
            self.engine.catalog.get_asr_engines(), {"/a.wav": "whisper-small", "/b.wav": ""}
        )

    def test_transcripts_are_cached_by_content_and_engine(self):
        catalog = self.engine.catalog
        self.assertIsNone(catalog.get_transcript("hash-a", "whisper-small"))

        catalog.save_transcript("hash-a", "whisper-small", "[00:05] hello there")
        catalog.save_transcript("hash-silent", "whisper-small", "")

        self.assertEqual(catalog.get_transcript("hash-a", "whisper-small"), "[00:05] hello there")
        self.assertEqual(catalog.get_transcript("hash-silent", "whisper-small"), "")
        self.assertIsNone(catalog.get_transcript("hash-a", "whisper-turbo"))

    def test_catalog_created_before_asr_tracking_is_upgraded(self):
        db_path = self.root / "legacy.db"
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE documents (path TEXT PRIMARY KEY, mtime_ns INTEGER NOT NULL, "
            "file_size INTEGER NOT NULL, content_hash TEXT NOT NULL, compressed_text BLOB, "
            "updated_at REAL NOT NULL, ocr_engine TEXT NOT NULL DEFAULT '')"
        )
        conn.execute("INSERT INTO documents VALUES ('/x.mp3', 1, 1, 'h', NULL, 0, '')")
        conn.commit()
        conn.close()

        catalog = CatalogDB(str(db_path))
        self.assertEqual(catalog.get_asr_engines(), {"/x.mp3": ""})
        self.assertIsNone(catalog.get_transcript("h", "whisper-small"))


if __name__ == "__main__":
    unittest.main()
