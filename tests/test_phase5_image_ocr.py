"""Image search tests: metadata, OCR preparation, the OCR pipeline and inputs that must not crash."""

import io
import os
import struct
import sys
import tempfile
import threading
import unittest
import zlib
from pathlib import Path
from unittest import mock

from PIL import Image, ImageDraw, ImageFont, PngImagePlugin

from smartlex.core import media_extractors, processor
from smartlex.core.catalog import CatalogDB
from smartlex.core.media_extractors import (
    extract_text_image,
    get_ocr_engine_id,
    read_image_metadata,
    register_ocr_backend,
    run_image_ocr,
)
from smartlex.core.native_engine import NativeIndex, SmartLexEngine, scan_directories, tokenize_keywords
from smartlex.core.processor import process_all_batches, process_files_parallel, run_ocr_pass
from smartlex.gui.result_formatting import parse_indexing_progress


def _real_ocr_available() -> bool:
    register_ocr_backend(None)
    return get_ocr_engine_id().startswith("rapidocr") or get_ocr_engine_id() == "tesseract"


requires_ocr = unittest.skipUnless(_real_ocr_available(), "no OCR engine installed")


def render_text(lines, size=(900, 260), background="white", colour="black", mode="RGB"):
    """Draws `lines` of large text, one per row, onto a new image."""
    img = Image.new(mode, size, background)
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=44)
    y = 30
    for line in lines:
        draw.text((30, y), line, fill=colour, font=font)
        y += 80
    return img


def words_in(text: str) -> set:
    return set(tokenize_keywords(text, include_stopwords=True))


class ImageTestCase(unittest.TestCase):
    def setUp(self):
        register_ocr_backend(None)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.engine = SmartLexEngine(
            index_file=str(self.root / "index.slex"),
            catalog_file=str(self.root / "catalog.db"),
        )

    def tearDown(self):
        register_ocr_backend(None)
        self.temp_dir.cleanup()

    def write_batch_file(self, paths) -> str:
        batch = self.root / "batch.txt"
        batch.write_text("\n".join(str(p) for p in paths), encoding="utf-8")
        return str(batch)


class TestImageMetadata(ImageTestCase):
    def test_png_text_chunks_are_indexed_without_field_names(self):
        path = self.root / "diagram.png"
        info = PngImagePlugin.PngInfo()
        info.add_text("Title", "Kubernetes Cluster Topology")
        info.add_text("Software", "Inkscape")
        Image.new("RGB", (640, 480), "white").save(path, pnginfo=info)

        metadata = read_image_metadata(str(path))
        self.assertIn("[Image PNG 640x480]", metadata)
        self.assertIn("Kubernetes Cluster Topology", metadata)
        self.assertIn("Inkscape", metadata)
        self.assertNotIn("software", words_in(metadata))
        self.assertNotIn("title", words_in(metadata))
        self.assertNotIn("rgb", words_in(metadata))

    def test_exif_fields_including_exif_sub_ifd_date(self):
        path = self.root / "photo.jpg"
        img = Image.new("RGB", (320, 240), "gray")
        exif = Image.Exif()
        exif[0x010F] = "Canon"                       # Make
        exif[0x0110] = "EOS 5D"                      # Model
        exif[0x010E] = "Sunset over Lake Geneva"     # ImageDescription
        exif[0x9C9E] = "holiday;switzerland\x00".encode("utf-16-le")  # XPKeywords
        exif.get_ifd(0x8769)[0x9003] = "2024:05:01 18:30:00"          # DateTimeOriginal
        img.save(path, exif=exif)

        metadata = read_image_metadata(str(path))
        for expected in ("Canon", "EOS 5D", "Sunset over Lake Geneva", "holiday;switzerland", "2024-05-01"):
            self.assertIn(expected, metadata)
        self.assertNotIn("\x00", metadata)
        self.assertNotIn("make", words_in(metadata))
        self.assertNotIn("30", words_in(metadata))

    def test_xmp_title_and_keywords(self):
        xmp = (
            '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
            'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/"><rdf:Description>'
            '<dc:title><rdf:Alt><rdf:li xml:lang="x-default">Wedding Reception</rdf:li></rdf:Alt></dc:title>'
            '<dc:subject><rdf:Bag><rdf:li>family</rdf:li><rdf:li>celebration</rdf:li></rdf:Bag></dc:subject>'
            "</rdf:Description></rdf:RDF></x:xmpmeta>"
        )
        path = self.root / "tagged.png"
        info = PngImagePlugin.PngInfo()
        info.add_itxt("XML:com.adobe.xmp", xmp)
        Image.new("RGB", (100, 100)).save(path, pnginfo=info)

        metadata = read_image_metadata(str(path))
        for expected in ("Wedding Reception", "family", "celebration"):
            self.assertIn(expected, metadata)

    def test_broken_xmp_is_ignored(self):
        path = self.root / "badxmp.png"
        info = PngImagePlugin.PngInfo()
        info.add_itxt("XML:com.adobe.xmp", "<x:xmpmeta><unclosed>")
        Image.new("RGB", (50, 50)).save(path, pnginfo=info)
        self.assertEqual(read_image_metadata(str(path)), "[Image PNG 50x50]")

    def test_sidecar_caption_is_read_and_size_capped(self):
        path = self.root / "scan.png"
        Image.new("RGB", (50, 50)).save(path)
        (self.root / "scan.txt").write_text("receipt from hardware store " + "x" * 3_000_000)
        metadata = read_image_metadata(str(path))
        self.assertIn("receipt from hardware store", metadata)
        self.assertLess(len(metadata), media_extractors.SIDECAR_MAX_BYTES + 200)


class TestUnusualFilesNeverCrash(ImageTestCase):
    def setUp(self):
        super().setUp()
        self.ocr_calls = []
        register_ocr_backend(None)

    def assert_safe(self, path: Path):
        """Metadata, OCR and full indexing must all succeed without raising."""
        read_image_metadata(str(path))
        run_image_ocr(str(path))
        extract_text_image(str(path))
        process_all_batches([self.write_batch_file([path])], engine=self.engine)
        self.assertIn(str(path), self.engine.keys())

    def test_empty_file(self):
        path = self.root / "empty.png"
        path.write_bytes(b"")
        self.assert_safe(path)

    def test_not_really_an_image(self):
        path = self.root / "page.jpg"
        path.write_text("<html><body>not an image</body></html>")
        self.assert_safe(path)

    def test_truncated_png(self):
        buffer = io.BytesIO()
        render_text(["Truncated"]).save(buffer, "PNG")
        path = self.root / "truncated.png"
        path.write_bytes(buffer.getvalue()[: len(buffer.getvalue()) // 2])
        self.assert_safe(path)

    def test_decompression_bomb_header(self):
        # A valid PNG header claiming 30000x30000 pixels with almost no data.
        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

        png = (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 30000, 30000, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00" * 10))
            + chunk(b"IEND", b"")
        )
        path = self.root / "bomb.png"
        path.write_bytes(png)
        self.assert_safe(path)

    def test_tiny_and_extreme_aspect_images(self):
        for name, size in (("dot.png", (1, 1)), ("tiny.png", (4, 4)), ("line.png", (1, 5000)), ("strip.png", (5000, 2))):
            path = self.root / name
            Image.new("RGB", size, "white").save(path)
            self.assert_safe(path)

    def test_fully_transparent_image(self):
        path = self.root / "transparent.png"
        Image.new("RGBA", (300, 300), (0, 0, 0, 0)).save(path)
        self.assertEqual(run_image_ocr(str(path)), "")
        self.assert_safe(path)

    def test_custom_backend_that_raises(self):
        path = self.root / "a.png"
        render_text(["hello"]).save(path)

        def broken_backend(_path):
            raise RuntimeError("model crashed")

        register_ocr_backend(broken_backend)
        self.assertEqual(run_image_ocr(str(path)), "")
        self.assert_safe(path)

    def test_file_deleted_before_extraction(self):
        path = self.root / "gone.png"
        render_text(["gone"]).save(path)
        batch = self.write_batch_file([path])
        path.unlink()
        process_all_batches([batch], engine=self.engine)
        self.assertEqual(run_image_ocr(str(path)), "")
        self.assertEqual(read_image_metadata(str(path)), "")

    def test_nul_bytes_in_text_do_not_break_indexing(self):
        path = self.root / "nul.png"
        Image.new("RGB", (40, 40)).save(path)
        register_ocr_backend(lambda _p: "alpha\x00beta gamma")
        process_files_parallel([str(path)], engine=self.engine)
        self.assertEqual([r["path"] for r in self.engine.search("gamma")], [str(path)])

    def test_extractor_exception_keeps_file_searchable_by_name(self):
        good = self.root / "good_notes.txt"
        good.write_text("searchable content")
        bad = self.root / "exploding_report.png"
        Image.new("RGB", (40, 40)).save(bad)

        original = processor.extract_text_from_file

        def flaky_extract(path, include_ocr=True):
            if path == str(bad):
                raise MemoryError("simulated")
            return original(path, include_ocr=include_ocr)

        with mock.patch.object(processor, "extract_text_from_file", flaky_extract):
            process_all_batches([self.write_batch_file([good, bad])], engine=self.engine)

        self.assertEqual([r["path"] for r in self.engine.search("searchable")], [str(good)])
        self.assertEqual([r["path"] for r in self.engine.search("exploding")], [str(bad)])

    @unittest.skipUnless(sys.platform.startswith("linux"), "only Linux allows non-UTF-8 file names")
    def test_scanner_skips_names_that_are_not_utf8(self):
        os.makedirs(self.root / "pics", exist_ok=True)
        with open(os.path.join(str(self.root / "pics").encode(), b"bad\xff\xfe.png"), "wb") as f:
            f.write(b"x")
        Image.new("RGB", (10, 10)).save(self.root / "pics" / "good.png")
        found = [Path(rec[0]).name for rec in scan_directories([str(self.root / "pics")], [".png"], 2)]
        self.assertEqual(found, ["good.png"])


class TestOcrPreparation(ImageTestCase):
    """Checks what reaches the OCR engine, without needing a real engine."""

    def capture_recognized_images(self, path):
        seen = []

        def fake_recognize(image):
            seen.append(image.copy())
            return ["word"]

        with mock.patch.object(media_extractors, "get_ocr_engine_id", return_value="test"), \
                mock.patch.object(media_extractors, "_recognize_lines", fake_recognize):
            text = run_image_ocr(str(path))
        return text, seen

    def test_blank_image_is_not_sent_to_ocr(self):
        path = self.root / "blank.png"
        Image.new("RGB", (1920, 1080), (250, 250, 250)).save(path)
        text, seen = self.capture_recognized_images(path)
        self.assertEqual((text, seen), ("", []))

    def test_transparent_background_gets_contrasting_fill(self):
        dark_text = self.root / "dark.png"
        render_text(["Logo"], background=(0, 0, 0, 0), colour=(0, 0, 0, 255), mode="RGBA").save(dark_text)
        _, seen = self.capture_recognized_images(dark_text)
        self.assertEqual(seen[0].mode, "RGB")
        self.assertEqual(seen[0].getpixel((1, 1)), (255, 255, 255))

        light_text = self.root / "light.png"
        render_text(["Logo"], background=(0, 0, 0, 0), colour=(255, 255, 255, 255), mode="RGBA").save(light_text)
        _, seen = self.capture_recognized_images(light_text)
        self.assertEqual(seen[0].getpixel((1, 1)), (0, 0, 0))

    def test_tall_screenshot_is_tiled_with_overlap_and_bounded(self):
        path = self.root / "tall.png"
        Image.new("RGB", (800, 6000), "white").save(path)
        img = Image.open(path)
        ImageDraw.Draw(img).rectangle((10, 10, 100, 100), fill="black")
        img.save(path)

        _, seen = self.capture_recognized_images(path)
        self.assertGreater(len(seen), 1)
        self.assertLessEqual(len(seen), media_extractors.MAX_OCR_TILES)
        self.assertTrue(all(tile.width == 800 for tile in seen))
        self.assertGreaterEqual(sum(tile.height for tile in seen), 6000)

        ranges = media_extractors._tile_ranges(100_000, 100)
        self.assertLessEqual(len(ranges), media_extractors.MAX_OCR_TILES)
        self.assertEqual((ranges[0][0], ranges[-1][1]), (0, 100_000))
        for (_, previous_end), (next_start, _) in zip(ranges, ranges[1:]):
            self.assertLess(next_start, previous_end)

    def test_sixteen_bit_image_keeps_contrast(self):
        path = self.root / "depth16.png"
        img = Image.new("I;16", (400, 200), 1000)
        ImageDraw.Draw(img).rectangle((50, 50, 150, 150), fill=3000)
        img.save(path)
        _, seen = self.capture_recognized_images(path)
        low, high = seen[0].convert("L").getextrema()
        self.assertGreater(high - low, 200)

    def test_exif_rotation_is_applied(self):
        path = self.root / "rotated.jpg"
        exif = Image.Exif()
        exif[0x0112] = 6  # stored sideways, display rotated 90 degrees
        render_text(["rotated"], size=(600, 200)).save(path, exif=exif)
        _, seen = self.capture_recognized_images(path)
        self.assertEqual(seen[0].size, (200, 600))

    def test_multi_page_tiff_reads_each_page(self):
        path = self.root / "scan.tiff"
        pages = [render_text([f"page {n}"]) for n in (1, 2, 3)]
        pages[0].save(path, save_all=True, append_images=pages[1:])
        text, seen = self.capture_recognized_images(path)
        self.assertEqual(len(seen), 3)
        self.assertIn("[Page 3] word", text)

    def test_animated_gif_reads_first_frame_only(self):
        path = self.root / "anim.gif"
        frames = [render_text([f"frame {n}"]) for n in range(5)]
        frames[0].save(path, save_all=True, append_images=frames[1:])
        _, seen = self.capture_recognized_images(path)
        self.assertEqual(len(seen), 1)

    def test_other_modes_are_converted(self):
        for mode, name in (("P", "palette.gif"), ("CMYK", "cmyk.jpg"), ("1", "bilevel.bmp"), ("LA", "gray_alpha.png")):
            path = self.root / name
            render_text(["mode"]).convert(mode).save(path)
            _, seen = self.capture_recognized_images(path)
            self.assertEqual(len(seen), 1, name)
            self.assertEqual(seen[0].mode, "RGB", name)


class TestTwoPhaseIndexing(ImageTestCase):
    def setUp(self):
        super().setUp()
        self.ocr_calls = []

        def counting_backend(path):
            self.ocr_calls.append(path)
            return f"ocr text of {Path(path).stem}"

        register_ocr_backend(counting_backend)

    def make_image(self, name, colour="white"):
        path = self.root / name
        img = Image.new("RGB", (200, 100), colour)
        ImageDraw.Draw(img).text((10, 10), name, fill="black")
        img.save(path)
        return path

    def sync(self, paths, **kwargs):
        return process_all_batches([self.write_batch_file(paths)], engine=self.engine, **kwargs)

    def test_each_image_is_read_once(self):
        paths = [self.make_image(f"img{n}.png") for n in range(6)]
        self.sync(paths)
        self.assertEqual(len(self.ocr_calls), 6)
        self.assertEqual([r["path"] for r in self.engine.search("img3")], [str(paths[3])])
        self.assertEqual([r["path"] for r in self.engine.search('"ocr text of img4"')], [str(paths[4])])

        self.sync(paths)
        self.assertEqual(len(self.ocr_calls), 6)

    def test_modified_image_is_read_again(self):
        path = self.make_image("receipt.png")
        self.sync([path])
        Image.new("RGB", (300, 300), "red").save(path)
        os.utime(path, ns=(1, 1))
        self.sync([path])
        self.assertEqual(len(self.ocr_calls), 2)

    def test_identical_copies_are_read_once(self):
        original = self.make_image("original.png")
        copy = self.root / "copy_of_original.png"
        copy.write_bytes(original.read_bytes())
        self.sync([original, copy])
        self.assertEqual(len(self.ocr_calls), 1)
        hits = {r["path"] for r in self.engine.search('"ocr text of original"')}
        self.assertEqual(hits, {str(original), str(copy)})

    def test_moved_image_keeps_text_without_new_ocr(self):
        path = self.make_image("whiteboard.png")
        self.sync([path])
        moved = self.root / "archive_whiteboard.png"
        path.rename(moved)
        self.sync([moved])
        self.assertEqual(len(self.ocr_calls), 1)
        self.assertEqual([r["path"] for r in self.engine.search("whiteboard")], [str(moved)])
        self.assertEqual(self.engine.catalog.get_ocr_engines()[str(moved)], "custom")

    def test_images_indexed_without_ocr_engine_are_read_once_one_is_installed(self):
        paths = [self.make_image(f"old{n}.png") for n in range(3)]
        register_ocr_backend(None)
        with mock.patch.object(processor, "get_ocr_engine_id", return_value=""):
            self.sync(paths)
        self.assertEqual(self.engine.search("ocr"), [])

        register_ocr_backend(lambda p: self.ocr_calls.append(p) or "invoice total")
        self.sync(paths)
        self.assertEqual(len(self.ocr_calls), 3)
        self.assertEqual(len(self.engine.search("invoice")), 3)

    def test_cancel_keeps_finished_images_and_resumes_later(self):
        paths = [self.make_image(f"batch{n}.png") for n in range(10)]
        self.sync(paths, should_cancel=lambda: len(self.ocr_calls) >= 3)
        self.assertEqual(len(self.ocr_calls), 3)
        self.assertEqual(len(self.engine.search('"ocr text"')), 3)

        self.sync(paths)
        self.assertEqual(len(self.ocr_calls), 10)
        self.assertEqual(len(self.engine.search('"ocr text"')), 10)

    def test_metadata_is_searchable_before_ocr_finishes(self):
        path = self.make_image("chart.png")
        searchable_during_ocr = []

        def slow_backend(p):
            searchable_during_ocr.append([r["path"] for r in self.engine.search("200x100")])
            return "late text"

        register_ocr_backend(slow_backend)
        self.sync([path])
        self.assertEqual(searchable_during_ocr, [[str(path)]])

    def test_progress_messages_drive_the_progress_bar(self):
        paths = [self.make_image(f"p{n}.png") for n in range(5)]
        messages = []
        self.sync(paths, progress_callback=messages.append)
        ocr_messages = [m for m in messages if m.startswith("Reading text in images")]
        self.assertEqual(ocr_messages[0], "Reading text in images: 0/5 images...")
        self.assertEqual(ocr_messages[-1], "Reading text in images: 5/5 images...")
        self.assertEqual(parse_indexing_progress(ocr_messages[1]), (4, 5))

    def test_engine_failure_mid_pass_leaves_images_for_retry(self):
        paths = [self.make_image(f"retry{n}.png") for n in range(3)]
        with mock.patch.object(processor, "get_ocr_engine_id", return_value=""):
            self.sync(paths)

        def backend_that_dies(path):
            register_ocr_backend(None)  # engine disappears after the first image
            return ""

        register_ocr_backend(backend_that_dies)
        with mock.patch.object(media_extractors, "_installed_rapidocr_version", return_value=""), \
                mock.patch.object(media_extractors.shutil, "which", return_value=None):
            self.sync(paths)
        self.assertEqual(set(self.engine.catalog.get_ocr_engines().values()), {""})

        register_ocr_backend(lambda p: "recovered text")
        self.sync(paths)
        self.assertEqual(len(self.engine.search("recovered")), 3)

    def test_run_ocr_pass_without_engine_does_nothing(self):
        path = self.make_image("x.png")
        register_ocr_backend(None)
        with mock.patch.object(processor, "get_ocr_engine_id", return_value=""):
            self.assertEqual(run_ocr_pass(self.engine, [str(path)]), 0)


class TestIndexRecovery(ImageTestCase):
    def test_rows_written_after_last_save_are_loaded(self):
        first = self.root / "first.txt"
        first.write_text("alpha")
        process_files_parallel([str(first)], engine=self.engine)
        self.engine.save()
        os.utime(self.engine.index_file, (1, 1))  # index file saved "long ago"

        second = self.root / "second.txt"
        second.write_text("bravo")
        process_files_parallel([str(second)], engine=self.engine)  # written, never saved

        reloaded = SmartLexEngine(self.engine.index_file, self.engine.catalog.db_path)
        self.assertTrue(reloaded.load())
        self.assertEqual([r["path"] for r in reloaded.search("bravo")], [str(second)])

    def test_old_index_format_is_rebuilt_from_catalog(self):
        notes = self.root / "notes.txt"
        notes.write_text("zebra crossing")
        process_files_parallel([str(notes)], engine=self.engine)
        self.engine.save()
        data = bytearray(Path(self.engine.index_file).read_bytes())
        data[4:8] = struct.pack("<I", 1)  # pretend it was written by format version 1
        Path(self.engine.index_file).write_bytes(bytes(data))

        fresh = SmartLexEngine(self.engine.index_file, self.engine.catalog.db_path)
        self.assertFalse(fresh.load())
        process_all_batches([self.write_batch_file([notes])], engine=fresh)
        self.assertEqual([r["path"] for r in fresh.search("zebra")], [str(notes)])

    def test_rebuild_keeps_every_file_when_files_also_moved_or_deleted(self):
        kept = [self.root / f"kept{n}.txt" for n in range(5)]
        for n, path in enumerate(kept):
            path.write_text(f"unique{n} words")
        moving = self.root / "moving.txt"
        moving.write_text("travelling content")
        doomed = self.root / "doomed.txt"
        doomed.write_text("soon deleted")
        process_files_parallel([str(p) for p in kept + [moving, doomed]], engine=self.engine)

        moved = self.root / "moved_here.txt"
        moving.rename(moved)
        doomed.unlink()

        # No usable index file, as after an upgrade or a corrupt save.
        fresh = SmartLexEngine(str(self.root / "missing.slex"), self.engine.catalog.db_path)
        self.assertFalse(fresh.load())
        process_all_batches([self.write_batch_file(kept + [moved])], engine=fresh)

        self.assertEqual(set(fresh.keys()), {str(p) for p in kept + [moved]})
        self.assertEqual([r["path"] for r in fresh.search("unique3")], [str(kept[3])])
        self.assertEqual([r["path"] for r in fresh.search("travelling")], [str(moved)])
        self.assertEqual(fresh.search("deleted"), [])

    def test_catalog_created_before_ocr_tracking_is_upgraded(self):
        import sqlite3

        db_path = self.root / "legacy.db"
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE documents (path TEXT PRIMARY KEY, mtime_ns INTEGER NOT NULL, "
            "file_size INTEGER NOT NULL, content_hash TEXT NOT NULL, compressed_text BLOB, "
            "updated_at REAL NOT NULL)"
        )
        conn.execute("INSERT INTO documents VALUES ('/x.png', 1, 1, 'h', NULL, 0)")
        conn.commit()
        conn.close()

        catalog = CatalogDB(str(db_path))
        self.assertEqual(catalog.get_ocr_engines(), {"/x.png": ""})


class TestTokenizerForOcrText(unittest.TestCase):
    def test_unicode_punctuation_separates_words(self):
        self.assertEqual(
            tokenize_keywords("•Item — Engine—fast “quoted” ✓done ■box", include_stopwords=True),
            ["item", "engine", "fast", "quoted", "done", "box"],
        )

    def test_soft_hyphen_joins_and_case_folds_accents(self):
        self.assertEqual(
            tokenize_keywords("infor­mation ÜBER Straße İstanbul МОСКВА café", include_stopwords=True),
            ["information", "über", "straße", "istanbul", "москва", "café"],
        )

    def test_invalid_utf8_bytes_do_not_crash(self):
        text = b"ok \xff\xfe \xe2\x82 end".decode("utf-8", errors="surrogateescape")
        index = NativeIndex()
        index.add_document("/t.txt", text.encode("utf-8", "replace").decode("utf-8"), 1, 1)
        self.assertEqual(len(index.search("end", 5)), 1)


@requires_ocr
class TestRealOcrAccuracy(ImageTestCase):
    """End-to-end checks with the installed OCR engine on rendered text."""

    def assert_reads(self, path, expected_words):
        found = words_in(run_image_ocr(str(path)))
        missing = set(expected_words) - found
        self.assertFalse(missing, f"OCR missed {missing} in {path.name}; read {sorted(found)}")

    def test_plain_screenshot_text(self):
        path = self.root / "plain.png"
        render_text(["Quarterly revenue forecast", "Invoice number 48213"]).save(path)
        self.assert_reads(path, {"quarterly", "revenue", "forecast", "invoice", "48213"})

    def test_black_text_on_transparent_background(self):
        path = self.root / "logo.png"
        render_text(["Transparent Banner"], background=(0, 0, 0, 0), colour=(0, 0, 0, 255), mode="RGBA").save(path)
        self.assert_reads(path, {"transparent", "banner"})

    def test_white_text_on_transparent_background(self):
        path = self.root / "logo_dark.png"
        render_text(["Midnight Theme"], background=(0, 0, 0, 0), colour=(255, 255, 255, 255), mode="RGBA").save(path)
        self.assert_reads(path, {"midnight", "theme"})

    def test_phone_photo_with_exif_rotation(self):
        path = self.root / "phone.jpg"
        upright = render_text(["Meeting notes Tuesday"], size=(900, 160))
        exif = Image.Exif()
        exif[0x0112] = 6
        upright.rotate(90, expand=True).save(path, exif=exif, quality=95)
        self.assert_reads(path, {"meeting", "notes", "tuesday"})

    def test_long_scrolling_screenshot(self):
        path = self.root / "scroll.png"
        img = Image.new("RGB", (900, 7000), "white")
        top = render_text(["Header section alpha"])
        bottom = render_text(["Footer section omega"])
        img.paste(top, (0, 0))
        img.paste(bottom, (0, 6700))
        img.save(path)
        self.assert_reads(path, {"header", "alpha", "footer", "omega"})

    def test_search_finds_image_by_visible_text(self):
        target = self.root / "error_dialog.png"
        render_text(["Connection refused", "Retry later"]).save(target)
        other = self.root / "holiday.png"
        render_text(["Beach sunset"]).save(other)

        process_all_batches([self.write_batch_file([target, other])], engine=self.engine)
        self.assertEqual([r["path"] for r in self.engine.search("connection refused")], [str(target)])
        self.assertEqual([r["path"] for r in self.engine.search('"beach sunset"')], [str(other)])
        self.assertIn("Connection refused", self.engine.search("refused")[0]["snippet"])
        self.assertEqual(self.engine.search("software"), [])

    def test_parallel_ocr_calls_are_safe(self):
        paths = []
        for n in range(4):
            path = self.root / f"thread{n}.png"
            render_text([f"Thread number {n}"]).save(path)
            paths.append(path)

        results = {}
        errors = []

        def worker(path):
            try:
                results[path] = run_image_ocr(str(path))
            except Exception as e:  # pragma: no cover - failure path
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(p,)) for p in paths]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        for n, path in enumerate(paths):
            self.assertIn("thread", words_in(results[path]))


if __name__ == "__main__":
    unittest.main()
