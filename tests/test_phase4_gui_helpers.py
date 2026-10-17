import os
import re
import tempfile
import unittest
from pathlib import Path

from smartlex.core.native_engine import NativeIndex
from smartlex.gui.file_actions import build_open_command, build_reveal_command
from smartlex.gui.result_formatting import (
    CATEGORY_EXTENSIONS,
    FILTER_CATEGORIES,
    build_search_query,
    describe_file,
    format_file_size,
    get_file_category,
    highlight_snippet_html,
    parse_indexing_progress,
)


class TestPhase4GuiHelpers(unittest.TestCase):
    """Qt-free search window logic: filter pills, snippet highlighting, details and progress."""


    def test_build_search_query_combines_text_and_pill(self):
        self.assertEqual(build_search_query("invoice 2024", ""), "invoice 2024")
        self.assertEqual(build_search_query("  invoice 2024  ", "doc"), "invoice 2024 type:doc")
        self.assertEqual(build_search_query("", "image"), "type:image")
        self.assertEqual(build_search_query("   ", ""), "")

    def test_users_own_type_filter_wins_over_pill(self):
        # The C++ engine UNIONs multiple ext:/type: filters, so the pill must not be appended
        self.assertEqual(build_search_query("logo ext:svg", "image"), "logo ext:svg")
        self.assertEqual(build_search_query("TYPE:audio interview", "video"), "TYPE:audio interview")
        # An exclusion like "-ext:pdf" or a bare "ext:" is NOT a filter, so the pill still applies
        self.assertEqual(build_search_query("report -ext:pdf", "doc"), "report -ext:pdf type:doc")
        self.assertEqual(build_search_query("ext:", "doc"), "ext: type:doc")

    def test_every_pill_except_all_has_a_category(self):
        self.assertEqual(FILTER_CATEGORIES[0].key, "")
        for category in FILTER_CATEGORIES[1:]:
            self.assertIn(category.key, CATEGORY_EXTENSIONS)

    def test_python_categories_match_cpp_type_filters(self):
        """Indexes one virtual file per extension and checks `type:<key>` in C++ returns exactly that set."""
        index = NativeIndex()
        all_extensions = set().union(*CATEGORY_EXTENSIONS.values()) | {".srt", ".unknownext"}
        for extension in sorted(all_extensions):
            index.add_document(f"/virtual/sample{extension}", "content")

        for category_key, expected_extensions in CATEGORY_EXTENSIONS.items():
            paths = index.search_paths(f"type:{category_key}", 1000)
            found_extensions = {Path(p).suffix for p in paths}
            self.assertEqual(found_extensions, expected_extensions, f"type:{category_key}")

        self.assertEqual(get_file_category("/x/Report.PDF"), "doc")
        self.assertEqual(get_file_category("/x/subtitles.srt"), "")


    def test_highlight_wraps_matched_words_case_insensitively(self):
        html_text = highlight_snippet_html("Total due for Invoice 2024", ["invoice", "2024"])
        highlighted = re.findall(r"<span[^>]*>(.*?)</span>", html_text)
        self.assertEqual(highlighted, ["Invoice", "2024"])

    def test_highlight_matches_stems_as_prefixes_at_word_start_only(self):
        html_text = highlight_snippet_html("computing and computers, not precompute", ["comput"])
        highlighted = re.findall(r"<span[^>]*>(.*?)</span>", html_text)
        self.assertEqual(highlighted, ["computing", "computers"])

    def test_highlight_escapes_html_and_never_matches_inside_entities(self):
        html_text = highlight_snippet_html("<script>alert(1)</script> Tom & Jerry", ["amp", "tom"])
        self.assertNotIn("<script>", html_text)
        self.assertIn("&lt;script&gt;", html_text)
        self.assertIn("&amp;", html_text)
        highlighted = re.findall(r"<span[^>]*>(.*?)</span>", html_text)
        self.assertEqual(highlighted, ["Tom"])

    def test_highlight_without_words_or_text(self):
        self.assertEqual(highlight_snippet_html("", ["x"]), "")
        self.assertEqual(highlight_snippet_html("a < b", []), "a &lt; b")


    def test_format_file_size(self):
        self.assertEqual(format_file_size(0), "0 B")
        self.assertEqual(format_file_size(1023), "1023 B")
        self.assertEqual(format_file_size(1024), "1.0 KB")
        self.assertEqual(format_file_size(1536), "1.5 KB")
        self.assertEqual(format_file_size(1024 * 1024), "1.0 MB")
        self.assertEqual(format_file_size(5 * 1024**3), "5.0 GB")

    def test_describe_existing_and_missing_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = os.path.join(temp_dir, "budget_2026.xlsx")
            Path(file_path).write_bytes(b"x" * 2048)

            details = describe_file(file_path)
            self.assertTrue(details.exists)
            self.assertEqual(details.name, "budget_2026.xlsx")
            self.assertEqual(details.folder, temp_dir)
            self.assertEqual(details.type_name, "XLSX Spreadsheet")
            self.assertEqual(details.size_text, "2.0 KB")
            self.assertRegex(details.modified_text, r"^\d{2} \w{3} \d{4}, \d{2}:\d{2}$")

        missing = describe_file("/definitely/missing/README")
        self.assertFalse(missing.exists)
        self.assertEqual(missing.type_name, "File")
        self.assertEqual(missing.size_text, "")


    def test_parse_indexing_progress(self):
        self.assertEqual(
            parse_indexing_progress("Indexing contents: 64/250 files (12 unchanged skipped)..."),
            (64, 250),
        )
        self.assertEqual(parse_indexing_progress("Indexing contents: 300/250 files"), (250, 250))
        self.assertIsNone(parse_indexing_progress("Indexing contents: 0/0 files"))
        self.assertIsNone(parse_indexing_progress("Scanning /home with C++20 parallel scanner..."))
        self.assertIsNone(parse_indexing_progress("Saving compressed binary index to disk..."))

    def test_open_and_reveal_commands_per_platform(self):
        path = "/home/user/My Docs/report.pdf"
        self.assertEqual(build_open_command(path, "linux"), ["xdg-open", path])
        self.assertEqual(build_open_command(path, "darwin"), ["open", path])
        self.assertEqual(build_reveal_command(path, "linux"), ["xdg-open", "/home/user/My Docs"])
        self.assertEqual(build_reveal_command(path, "darwin"), ["open", "-R", path])
        windows_command = build_reveal_command(path, "win32")
        self.assertEqual(windows_command[:2], ["explorer", "/select,"])


if __name__ == "__main__":
    unittest.main()

