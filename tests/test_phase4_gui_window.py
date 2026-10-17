import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Render Qt widgets without a visible window (works on CI and headless machines)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt5.QtCore import Qt
    from PyQt5.QtTest import QTest
    from PyQt5.QtWidgets import QApplication, QLabel

    PYQT5_AVAILABLE = True
except ImportError:
    PYQT5_AVAILABLE = False


@unittest.skipUnless(PYQT5_AVAILABLE, "PyQt5 is not installed")
class TestPhase4SearchWindow(unittest.TestCase):
    """Drives the real search window offscreen: cards, filters, progress and file actions."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        # Imported here so the module can still be collected when PyQt5 is missing
        from smartlex.core.native_engine import SmartLexEngine
        from smartlex.core.processor import process_files_parallel
        from smartlex.gui.main_window import MyWidget

        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

        self.notes_path = self.root / "revenue_notes.md"
        self.sheet_path = self.root / "revenue_table.csv"
        self.notes_path.write_text(
            "Meeting notes: quarterly revenue forecast looks strong.", encoding="utf-8"
        )
        self.sheet_path.write_text("region,revenue\nnorth,1200\n", encoding="utf-8")

        self.engine = SmartLexEngine(
            index_file=str(self.root / "test.slex"),
            catalog_file=str(self.root / "test.db"),
        )
        process_files_parallel([str(self.notes_path), str(self.sheet_path)], engine=self.engine)

        # Keep the real-time watcher scoped to the temp folder instead of the home directory
        self.drives_patch = mock.patch(
            "smartlex.gui.main_window.get_available_drives", return_value=[str(self.root)]
        )
        self.drives_patch.start()

        self.window = MyWidget([], self.engine, start_sync_immediately=False)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.drives_patch.stop()
        self.temp_dir.cleanup()

    def _search(self, text):
        self.window.search_input.setText(text)
        self.window.search_timer.stop()  # run synchronously instead of after the debounce
        self.window.perform_search()
        self.app.processEvents()

    def _result_paths(self):
        result_list = self.window.result_list
        return [result_list.item(row).data(Qt.UserRole) for row in range(result_list.count())]

    def _click_pill(self, category_key):
        button = self.window.filter_bar._buttons_by_key[category_key]
        QTest.mouseClick(button, Qt.LeftButton)
        self.app.processEvents()


    def test_results_are_rendered_as_cards_with_highlighted_snippets(self):
        self._search("forecast")

        self.assertEqual(self._result_paths(), [str(self.notes_path)])
        self.assertEqual(self.window.results_label.text(), "Results: 1")

        result_list = self.window.result_list
        card = result_list.itemWidget(result_list.item(0))
        self.assertEqual(card.objectName(), "ResultCard")

        labels = {label.objectName(): label for label in card.findChildren(QLabel)}
        self.assertEqual(labels["CardTitle"].text(), "revenue_notes.md")
        self.assertEqual(labels["CardPath"].text(), str(self.root))
        self.assertIn("forecast</span>", labels["CardSnippet"].text())
        self.assertTrue(labels["CardMeta"].text().startswith("MD Document"))

        # The card fills the list width and is tall enough for its wrapped content
        self.assertGreaterEqual(card.width(), result_list.viewport().width() - 20)
        self.assertGreaterEqual(card.height(), card.minimumSizeHint().height())

    def test_filter_pills_narrow_results_and_support_browsing(self):
        self._search("revenue")
        self.assertEqual(set(self._result_paths()), {str(self.notes_path), str(self.sheet_path)})

        self._click_pill("sheet")
        self.assertEqual(self._result_paths(), [str(self.sheet_path)])

        # Pill only, empty search box: lists every file of that category
        self.window.search_input.clear()
        self.window.search_timer.stop()
        self._click_pill("doc")
        self.assertEqual(self._result_paths(), [str(self.notes_path)])

        # No images indexed: a single, non-openable message row
        self._click_pill("image")
        self.assertEqual(self._result_paths(), [None])
        self.assertEqual(self.window.results_label.text(), "Results: 0")

        # "All" with an empty box clears the list; Escape/Clear resets the pill
        self._click_pill("")
        self.assertEqual(self.window.result_list.count(), 0)
        self._click_pill("video")
        self.window.clear_search()
        self.assertEqual(self.window.filter_bar.current_category(), "")

    def test_progress_bar_follows_indexing_messages(self):
        progress_bar = self.window.index_progress
        self.assertFalse(progress_bar.isVisible())

        progress_bar.setVisible(True)
        self.window._on_indexing_progress("Indexing contents: 32/128 files (0 unchanged skipped)...")
        self.assertEqual((progress_bar.maximum(), progress_bar.value()), (128, 32))
        self.assertEqual(self.window.status_label.text().split(" (")[0], "Indexing contents: 32/128 files")

        self.window._on_indexing_progress("Saving compressed binary index to disk...")
        self.assertEqual(progress_bar.maximum(), 0)  # busy animation

        self.window._on_indexing_thread_stopped()
        self.assertFalse(progress_bar.isVisible())
        self.assertTrue(self.window.sync_button.isEnabled())

    def test_enter_opens_selected_result_exactly_once(self):
        self._search("forecast")
        with mock.patch("smartlex.gui.main_window.open_file_with_default_app") as open_mock:
            QTest.keyClick(self.window.result_list, Qt.Key_Return)
            self.app.processEvents()
        open_mock.assert_called_once_with(str(self.notes_path))

    def test_reveal_and_copy_path_actions(self):
        self._search("forecast")

        with mock.patch("smartlex.gui.main_window.reveal_in_file_manager") as reveal_mock:
            self.window._reveal_selected_result()
        reveal_mock.assert_called_once_with(str(self.notes_path))

        QApplication.clipboard().clear()
        self.window._copy_selected_result_path()
        self.assertEqual(QApplication.clipboard().text(), str(self.notes_path))

    def test_card_shows_untrusted_names_and_snippets_safely(self):
        from smartlex.gui.search_widgets import ResultCard

        card = ResultCard(
            {
                "path": "/missing/<b>bold_file.txt",
                "snippet": "Tom & Jerry <script>",
                "matched_words": ["tom"],
            }
        )
        labels = {label.objectName(): label for label in card.findChildren(QLabel)}
        self.assertEqual(labels["CardTitle"].text(), "<b>bold_file.txt")
        self.assertEqual(labels["CardTitle"].textFormat(), Qt.PlainText)
        self.assertIn("&lt;script&gt;", labels["CardSnippet"].text())
        self.assertIn("File no longer exists", labels["CardMeta"].text())


if __name__ == "__main__":
    unittest.main()

