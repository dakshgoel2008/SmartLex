import struct
import tempfile
import unittest
import wave
import zipfile
from pathlib import Path

from PIL import Image, PngImagePlugin

from smartlex.core.media_extractors import (
    register_asr_backend,
    register_ocr_backend,
)
from smartlex.core.native_engine import SmartLexEngine
from smartlex.core.processor import process_files_parallel


class TestPhase3Multimodal(unittest.TestCase):
    """End-to-end tests for office, image, audio and video extraction and type: filters."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.index_path = str(self.root / "test_phase3.slex")
        self.catalog_path = str(self.root / "test_phase3.db")
        self.engine = SmartLexEngine(
            index_file=self.index_path,
            catalog_file=self.catalog_path,
        )
        # Keeps tests from loading a real speech model when one is installed.
        register_asr_backend(lambda path: "")

    def tearDown(self):
        register_ocr_backend(None)
        register_asr_backend(None)
        self.temp_dir.cleanup()


    def _create_sample_xlsx(self, path: Path) -> None:
        workbook_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
            <sheets>
                <sheet name="Q4 Financials" sheetId="1" r:id="rId1" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/>
            </sheets>
        </workbook>"""

        shared_strings_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="3" uniqueCount="3">
            <si><t>Department</t></si>
            <si><t>Photonic Quantum Hardware</t></si>
            <si><t>Approved Budget</t></si>
        </sst>"""

        sheet1_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
            <sheetData>
                <row r="1">
                    <c r="A1" t="s"><v>0</v></c>
                    <c r="B1" t="s"><v>2</v></c>
                </row>
                <row r="2">
                    <c r="A2" t="s"><v>1</v></c>
                    <c r="B2"><v>4850000</v></c>
                </row>
            </sheetData>
        </worksheet>"""

        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("xl/workbook.xml", workbook_xml)
            zf.writestr("xl/sharedStrings.xml", shared_strings_xml)
            zf.writestr("xl/worksheets/sheet1.xml", sheet1_xml)

    def _create_sample_pptx(self, path: Path) -> None:
        slide1_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
               xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
            <p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r>
                <a:t>Executive Summary Overview</a:t>
            </a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld>
        </p:sld>"""

        slide2_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
               xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
            <p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r>
                <a:t>Zero Latency Hyperscale Architecture</a:t>
            </a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld>
        </p:sld>"""

        notes2_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <p:notes xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
                 xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
            <p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r>
                <a:t>Mention the custom Rust and C++20 SIMD kernels during demo.</a:t>
            </a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld>
        </p:notes>"""

        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("ppt/slides/slide1.xml", slide1_xml)
            zf.writestr("ppt/slides/slide2.xml", slide2_xml)
            zf.writestr("ppt/notesSlides/notesSlide2.xml", notes2_xml)

    def _create_sample_epub(self, path: Path) -> None:
        opf_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <package xmlns="http://www.idpf.org/2007/opf" version="2.0">
            <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
                <dc:title>Distributed Consensus Systems</dc:title>
                <dc:creator>Dr. Leslie Lamport</dc:creator>
            </metadata>
            <manifest>
                <item id="ch1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
            </manifest>
            <spine>
                <itemref idref="ch1"/>
            </spine>
        </package>"""

        ch1_xhtml = """<html><body><h1>Chapter 1: Paxos and Raft</h1>
        <p>Byzantine fault tolerance guarantees safety even when nodes equivocate.</p>
        </body></html>"""

        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("OEBPS/content.opf", opf_xml)
            zf.writestr("OEBPS/chapter1.xhtml", ch1_xhtml)

    def _create_sample_png(self, path: Path) -> None:
        img = Image.new("RGB", (1280, 720), color=(20, 40, 80))
        meta = PngImagePlugin.PngInfo()
        meta.add_text("Title", "Kubernetes Cluster Topology Diagram")
        meta.add_text("Author", "Platform Engineering Team")
        img.save(path, "PNG", pnginfo=meta)

    def _create_sample_mp3_with_id3v2(self, path: Path) -> None:
        def make_id3v23_frame(frame_id: bytes, text: str) -> bytes:
            payload = b"\x03" + text.encode("utf-8")  # 0x03 = UTF-8
            return frame_id + struct.pack(">I", len(payload)) + b"\x00\x00" + payload

        frames = (
            make_id3v23_frame(b"TIT2", "Keynote on Neuromorphic Computing")
            + make_id3v23_frame(b"TPE1", "Prof. Carver Mead")
            + make_id3v23_frame(b"TALB", "Silicon Valley Symposium 2026")
        )
        size = len(frames)
        synchsafe = bytes(
            [
                (size >> 21) & 0x7F,
                (size >> 14) & 0x7F,
                (size >> 7) & 0x7F,
                size & 0x7F,
            ]
        )
        header = b"ID3\x03\x00\x00" + synchsafe
        path.write_bytes(header + frames + b"\xff\xfb\x90\x64" * 64)

    def _create_sample_wav(self, path: Path) -> None:
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b"\x00\x00\x00\x00" * 16000)  # 1 second stereo 16kHz

    def _create_sample_mp4_with_srt(self, mp4_path: Path, srt_path: Path) -> None:
        # Build minimal MP4 with mvhd (150 seconds = 02:30) and ©nam title atom
        mvhd_payload = (
            b"\x00\x00\x00\x00"  # version 0 + flags
            + struct.pack(">IIII", 0, 0, 1000, 150000)  # timescale=1000, duration=150000 (150s)
            + b"\x00" * 16
        )
        mvhd_atom = struct.pack(">I", 8 + len(mvhd_payload)) + b"mvhd" + mvhd_payload

        title_bytes = b"Autonomous Robotics Field Test"
        data_atom = (
            struct.pack(">I", 16 + len(title_bytes))
            + b"data"
            + b"\x00\x00\x00\x01\x00\x00\x00\x00"
            + title_bytes
        )
        nam_atom = struct.pack(">I", 8 + len(data_atom)) + b"\xa9nam" + data_atom
        mp4_path.write_bytes(b"\x00\x00\x00\x14ftypisom" + mvhd_atom + nam_atom)

        srt_content = """1
00:00:12,000 --> 00:00:16,500
Initializing lidar calibration sequence on rover alpha.

2
00:02:18,400 --> 00:02:23,000
Obstacle avoidance trajectory converged using reinforcement learning.
"""
        srt_path.write_text(srt_content, encoding="utf-8")


    def test_1_office_and_ebook_extractors(self):
        """Verifies .xlsx, .pptx (with speaker notes), and .epub indexing & location markers."""
        xlsx_path = self.root / "budget_2026.xlsx"
        pptx_path = self.root / "architecture_deck.pptx"
        epub_path = self.root / "consensus_book.epub"

        self._create_sample_xlsx(xlsx_path)
        self._create_sample_pptx(pptx_path)
        self._create_sample_epub(epub_path)

        process_files_parallel(
            [str(xlsx_path), str(pptx_path), str(epub_path)],
            engine=self.engine,
        )

        # 1. Search Excel spreadsheet content & numeric values
        xlsx_hits = self.engine.search("photonic quantum 4850000")
        self.assertEqual(len(xlsx_hits), 1)
        self.assertEqual(xlsx_hits[0]["path"], str(xlsx_path))
        self.assertIn("[Sheet: Q4 Financials]", xlsx_hits[0]["snippet"])

        # 2. Search PowerPoint slide + speaker notes
        pptx_hits = self.engine.search("hyperscale simd kernels")
        self.assertEqual(len(pptx_hits), 1)
        self.assertEqual(pptx_hits[0]["path"], str(pptx_path))
        self.assertIn("[Slide 2]", pptx_hits[0]["snippet"])

        # 3. Search EPUB metadata + chapter content
        epub_hits = self.engine.search("lamport byzantine equivocate")
        self.assertEqual(len(epub_hits), 1)
        self.assertEqual(epub_hits[0]["path"], str(epub_path))

    def test_2_image_metadata_and_pluggable_ocr(self):
        """Verifies PNG metadata chunks, resolution tags, and OCR text indexing."""
        png_path = self.root / "cluster_screenshot.png"
        self._create_sample_png(png_path)

        # Register simulated OCR backend
        register_ocr_backend(
            lambda p: "ERROR 503 Envoy upstream connect error or disconnect reset before headers"
        )

        process_files_parallel([str(png_path)], engine=self.engine)

        # Search by PNG embedded Title + OCR error message
        hits = self.engine.search("kubernetes envoy 503 upstream")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["path"], str(png_path))
        self.assertIn("Envoy", hits[0]["snippet"])

        # Search by image resolution
        res_hits = self.engine.search("1280x720")
        self.assertEqual(len(res_hits), 1)
        self.assertEqual(res_hits[0]["path"], str(png_path))

    def test_3_audio_id3_wav_and_timestamped_asr(self):
        """Verifies MP3 ID3v2 binary tags, WAV header, and [MM:SS] timestamped ASR."""
        mp3_path = self.root / "symposium_keynote.mp3"
        wav_path = self.root / "board_meeting.wav"

        self._create_sample_mp3_with_id3v2(mp3_path)
        self._create_sample_wav(wav_path)

        # Register simulated Whisper ASR backend for WAV file
        register_asr_backend(
            lambda p: (
                "[00:15] Welcome everyone to the third quarter board meeting.\n"
                "[04:42] We approved the acquisition of the semiconductor foundry in Dresden."
            )
            if p.endswith(".wav")
            else ""
        )

        process_files_parallel([str(mp3_path), str(wav_path)], engine=self.engine)

        # 1. Search MP3 by ID3v2 Artist & Title
        mp3_hits = self.engine.search("carver mead neuromorphic")
        self.assertEqual(len(mp3_hits), 1)
        self.assertEqual(mp3_hits[0]["path"], str(mp3_path))

        # 2. Search WAV by spoken phrase at 04:42 and verify timestamp is in snippet
        wav_hits = self.engine.search("semiconductor foundry dresden")
        self.assertEqual(len(wav_hits), 1)
        self.assertEqual(wav_hits[0]["path"], str(wav_path))
        self.assertIn("[04:42]", wav_hits[0]["snippet"])

    def test_4_video_mp4_atoms_and_sidecar_srt_timestamps(self):
        """Verifies MP4 metadata atoms and sidecar .srt subtitle [MM:SS] extraction."""
        mp4_path = self.root / "rover_test.mp4"
        srt_path = self.root / "rover_test.en.srt"
        self._create_sample_mp4_with_srt(mp4_path, srt_path)

        process_files_parallel([str(mp4_path)], engine=self.engine)

        # Search for spoken subtitle line at 02:18
        hits = self.engine.search("obstacle avoidance reinforcement learning")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["path"], str(mp4_path))
        self.assertIn("[02:18]", hits[0]["snippet"])

    def test_5_cpp_semantic_category_filters(self):
        """Verifies C++20 `type:image`, `type:audio`, `type:video`, `type:sheet`, `type:slide` filters."""
        xlsx_path = self.root / "metrics.xlsx"
        pptx_path = self.root / "pitch.pptx"
        png_path = self.root / "diagram.png"
        mp3_path = self.root / "podcast.mp3"
        mp4_path = self.root / "demo.mp4"
        srt_path = self.root / "demo.srt"

        self._create_sample_xlsx(xlsx_path)
        self._create_sample_pptx(pptx_path)
        self._create_sample_png(png_path)
        self._create_sample_mp3_with_id3v2(mp3_path)
        self._create_sample_mp4_with_srt(mp4_path, srt_path)

        process_files_parallel(
            [
                str(xlsx_path),
                str(pptx_path),
                str(png_path),
                str(mp3_path),
                str(mp4_path),
            ],
            engine=self.engine,
        )

        # Filter-only category queries
        img_hits = self.engine.search("type:image")
        self.assertEqual([h["path"] for h in img_hits], [str(png_path)])

        audio_hits = self.engine.search("type:audio")
        self.assertEqual([h["path"] for h in audio_hits], [str(mp3_path)])

        video_hits = self.engine.search("type:video")
        self.assertEqual([h["path"] for h in video_hits], [str(mp4_path)])

        sheet_hits = self.engine.search("type:sheet")
        self.assertEqual([h["path"] for h in sheet_hits], [str(xlsx_path)])

        slide_hits = self.engine.search("type:slide")
        self.assertEqual([h["path"] for h in slide_hits], [str(pptx_path)])


if __name__ == "__main__":
    unittest.main()
