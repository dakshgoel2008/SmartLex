"""Audio search tests: speech-to-text stays out of the content pass, and its results are stored."""

import sqlite3
import tempfile
import unittest
import wave
from pathlib import Path

from smartlex.core.catalog import CatalogDB
from smartlex.core.media_extractors import register_asr_backend
from smartlex.core.native_engine import SmartLexEngine
from smartlex.core.processor import process_all_batches
from smartlex.core.watcher import RealTimeFileWatcher


def write_silent_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 16000)


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

    def make_wav_with_subtitles(self, name: str) -> Path:
        wav_path = self.root / f"{name}.wav"
        write_silent_wav(wav_path)
        (self.root / f"{name}.srt").write_text(
            "1\n00:00:02,000 --> 00:00:04,000\nwelcome to the planning meeting\n",
            encoding="utf-8",
        )
        return wav_path


class TestAsrLeftOutOfContentPass(AudioTestCase):
    def test_sync_indexes_audio_without_transcribing(self):
        wav_path = self.make_wav_with_subtitles("meeting")
        batch_file = self.root / "batch.txt"
        batch_file.write_text(f"{wav_path}\n", encoding="utf-8")

        process_all_batches([str(batch_file)], engine=self.engine)

        self.assertEqual(self.transcribed, [])
        self.assertEqual([h["path"] for h in self.engine.search("planning meeting")], [str(wav_path)])
        self.assertEqual(self.engine.search("quarterly budget"), [])
        self.assertEqual(self.engine.catalog.get_asr_engines(), {str(wav_path): ""})

    def test_watcher_indexes_audio_without_transcribing(self):
        wav_path = self.make_wav_with_subtitles("standup")
        watcher = RealTimeFileWatcher(self.engine, [str(self.root)], [".wav"])

        self.assertTrue(watcher._index_file(str(wav_path)))

        self.assertEqual(self.transcribed, [])
        self.assertEqual([h["path"] for h in self.engine.search("planning meeting")], [str(wav_path)])


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
