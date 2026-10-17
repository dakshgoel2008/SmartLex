import time
from pathlib import Path

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QIcon, QKeySequence
from PyQt5.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QShortcut,
    QVBoxLayout,
    QWidget,
)

from smartlex.core.config import load_config
from smartlex.core.index_manager import generate_autocomplete
from smartlex.core.logger import setup_logger
from smartlex.core.native_engine import SmartLexEngine
from smartlex.core.scanner import get_available_drives
from smartlex.core.search_engine import search, search_with_snippets
from smartlex.core.watcher import RealTimeFileWatcher
from smartlex.gui.file_actions import open_file_with_default_app, reveal_in_file_manager
from smartlex.gui.result_formatting import build_search_query, parse_indexing_progress
from smartlex.gui.search_widgets import FilterPillBar, ResultListWidget
from smartlex.gui.threads import IndexingThread
from smartlex.gui.widgets import CustomCompleter

logger = setup_logger(__name__)

# Colour scheme of the status bar at the bottom of the window, per message kind
STATUS_STYLES = {
    "idle": "padding: 10px; background-color: #ffffff; border-radius: 8px; border: 1px solid #e0e6ed; color: #7f8c8d; font-weight: bold;",
    "info": "padding: 10px; background-color: #f0f7ff; border-radius: 8px; border: 1px solid #bae6fd; color: #0284c7; font-weight: bold;",
    "success": "padding: 10px; background-color: #f0fdf4; border-radius: 8px; border: 1px solid #bbf7d0; color: #166534; font-weight: bold;",
    "warning": "padding: 10px; background-color: #fefce8; border-radius: 8px; border: 1px solid #fef08a; color: #a16207; font-weight: bold;",
    "error": "padding: 10px; background-color: #fdf2f2; border-radius: 8px; border: 1px solid #fecaca; color: #ef4444; font-weight: bold;",
}


class MyWidget(QWidget):
    watcher_updated_signal = pyqtSignal()

    def __init__(self, autocomplete_words, index_data, start_sync_immediately=False):
        super().__init__()
        self.index_data = index_data
        self.autocomplete_words = autocomplete_words
        self.indexing_thread = None
        self.file_watcher = None

        self.search_timer = QTimer()
        self.search_timer.setSingleShot(True)
        self.search_timer.timeout.connect(self._execute_search)

        self.watcher_updated_signal.connect(self._on_watcher_updated)
        self.initUI()

        # Start real-time filesystem watcher if using SmartLexEngine
        self._start_realtime_watcher()

        if start_sync_immediately:
            QTimer.singleShot(50, self.trigger_incremental_sync)

    def _start_realtime_watcher(self):
        if isinstance(self.index_data, SmartLexEngine) and self.file_watcher is None:
            cfg = load_config()
            self.file_watcher = RealTimeFileWatcher(
                engine=self.index_data,
                watch_roots=get_available_drives(),
                supported_extensions=cfg["SUPPORTED_FORMATS"],
                on_index_updated=lambda _eng: self.watcher_updated_signal.emit(),
            )
            self.file_watcher.start()

    def _on_watcher_updated(self):
        if isinstance(self.index_data, SmartLexEngine):
            cfg = load_config()
            words = generate_autocomplete(self.index_data, cfg["AUTOCOMPLETE_WORDS"])
            self.update_index(self.index_data, words, quiet=True)
            if self._current_query():
                self.perform_search()

    def closeEvent(self, event):
        if self.file_watcher is not None:
            self.file_watcher.stop()
        if self.indexing_thread is not None and self.indexing_thread.isRunning():
            self.indexing_thread.cancel()
            # The thread stops at its next batch boundary; one OCR call can take a few
            # seconds. Waiting avoids Qt aborting on a thread destroyed while running.
            self.indexing_thread.wait(15000)
        super().closeEvent(event)

    def initUI(self):
        self.setWindowTitle("SmartLex Search Engine (C++20 Core)")
        icon_path = Path("icon.png")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.setMinimumSize(900, 650)
        self.resize(1000, 700)

        self.setStyleSheet(
            """
            QWidget {
                background-color: #f7f9fc;
                font-family: 'Segoe UI', Arial, sans-serif;
                color: #2c3e50;
            }
            QLabel {
                color: #34495e;
            }
            QLabel#TitleLabel {
                color: #2c3e50;
                font-size: 28pt;
                font-weight: 800;
                margin-top: 15px;
                margin-bottom: 15px;
            }
            QLabel#ResultsHeader {
                font-size: 14pt;
                font-weight: bold;
                color: #2c3e50;
            }
            QLabel#HelpLabel {
                color: #7f8c8d;
                font-size: 10pt;
                font-style: italic;
            }
            QLineEdit {
                padding: 12px 18px;
                font-size: 14pt;
                border: 2px solid #e0e6ed;
                border-radius: 12px;
                background-color: #ffffff;
                selection-background-color: #3498db;
            }
            QLineEdit:focus {
                border: 2px solid #3498db;
            }
            QPushButton {
                padding: 10px 25px;
                font-size: 13pt;
                font-weight: bold;
                border-radius: 10px;
            }
            QPushButton#SearchButton {
                background-color: #3498db;
                color: white;
                border: none;
            }
            QPushButton#SearchButton:hover {
                background-color: #2980b9;
            }
            QPushButton#SearchButton:pressed {
                background-color: #21618c;
            }
            QPushButton#ClearButton, QPushButton#SyncButton {
                background-color: #ffffff;
                color: #7f8c8d;
                border: 2px solid #bdc3c7;
            }
            QPushButton#ClearButton:hover, QPushButton#SyncButton:hover {
                background-color: #ecf0f1;
                color: #2c3e50;
            }
            QPushButton#ClearButton:pressed, QPushButton#SyncButton:pressed {
                background-color: #bdc3c7;
            }
            QPushButton#FilterPill {
                padding: 6px 14px;
                font-size: 10pt;
                font-weight: 600;
                border-radius: 15px;
                background-color: #ffffff;
                color: #5d6d7e;
                border: 1px solid #d5dde5;
            }
            QPushButton#FilterPill:hover {
                background-color: #ecf0f1;
                color: #2c3e50;
            }
            QPushButton#FilterPill:checked {
                background-color: #3498db;
                color: white;
                border: 1px solid #3498db;
            }
            QProgressBar#IndexProgress {
                border: 1px solid #e0e6ed;
                border-radius: 6px;
                background-color: #ffffff;
                text-align: center;
                font-size: 9pt;
                color: #2c3e50;
            }
            QProgressBar#IndexProgress::chunk {
                background-color: #3498db;
                border-radius: 5px;
            }
            QListWidget {
                border: 2px solid #e0e6ed;
                border-radius: 12px;
                background-color: #ffffff;
                font-size: 13pt;
                padding: 6px;
                outline: none;
            }
            QListWidget::item {
                border-bottom: 1px solid #f0f3f6;
                color: #34495e;
            }
            QListWidget::item:hover {
                background-color: #f7f9fc;
            }
            QListWidget::item:selected {
                background-color: #e8f4fd;
                color: #2980b9;
                border-radius: 8px;
            }
            QFrame#ResultCard, QFrame#ResultCard QLabel {
                background-color: transparent;
            }
            QLabel#CardIcon {
                font-size: 22pt;
            }
            QLabel#CardTitle {
                font-size: 13pt;
                font-weight: 700;
                color: #2c3e50;
            }
            QLabel#CardPath {
                font-size: 9pt;
                color: #7f8c8d;
            }
            QLabel#CardSnippet {
                font-size: 11pt;
                color: #34495e;
            }
            QLabel#CardMeta {
                font-size: 9pt;
                color: #95a5a6;
            }
            QListView::item {
                padding: 8px;
            }
            QMenu {
                background-color: #ffffff;
                border: 1px solid #e0e6ed;
                padding: 4px;
            }
            QMenu::item {
                padding: 6px 24px;
                border-radius: 6px;
            }
            QMenu::item:selected {
                background-color: #e8f4fd;
                color: #2980b9;
            }
            QScrollBar:vertical {
                border: none;
                background: #f7f9fc;
                width: 12px;
                border-radius: 6px;
                margin: 0px 0px 0px 0px;
            }
            QScrollBar::handle:vertical {
                background: #bdc3c7;
                min-height: 30px;
                border-radius: 6px;
            }
            QScrollBar::handle:vertical:hover {
                background: #95a5a6;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """
        )

        layout = QVBoxLayout()
        layout.setSpacing(12)
        layout.setContentsMargins(30, 20, 30, 20)

        title_label = QLabel("SmartLex Search Engine")
        title_label.setObjectName("TitleLabel")
        title_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(title_label)

        search_layout = QHBoxLayout()

        completer = CustomCompleter(self.autocomplete_words, self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setCompletionMode(completer.PopupCompletion)

        self.search_input = QLineEdit()
        self.search_input.setCompleter(completer)
        self.search_input.setPlaceholderText(
            'Search keywords, "exact phrase", ext:pdf, type:image, path:docs, -exclude'
        )
        self.search_input.setMinimumHeight(50)
        self.search_input.textChanged.connect(self._on_text_changed)
        search_layout.addWidget(self.search_input, stretch=4)

        self.search_button = QPushButton("Search")
        self.search_button.setObjectName("SearchButton")
        self.search_button.setMinimumHeight(50)
        self.search_button.setMinimumWidth(120)
        self.search_button.clicked.connect(self.perform_search)
        self.search_button.setDefault(True)
        search_layout.addWidget(self.search_button)

        self.clear_button = QPushButton("Clear")
        self.clear_button.setObjectName("ClearButton")
        self.clear_button.setMinimumHeight(50)
        self.clear_button.setMinimumWidth(100)
        self.clear_button.clicked.connect(self.clear_search)
        search_layout.addWidget(self.clear_button)

        self.sync_button = QPushButton("Sync")
        self.sync_button.setObjectName("SyncButton")
        self.sync_button.setMinimumHeight(50)
        self.sync_button.setMinimumWidth(90)
        self.sync_button.setToolTip("Incrementally index new, modified, or moved files")
        self.sync_button.clicked.connect(self.trigger_incremental_sync)
        search_layout.addWidget(self.sync_button)

        layout.addLayout(search_layout)

        # Category filter pills: All / Docs / Sheets / Slides / Images / Audio / Video / Code
        self.filter_bar = FilterPillBar()
        self.filter_bar.category_changed.connect(self._on_category_changed)
        layout.addWidget(self.filter_bar)

        # Live indexing progress (hidden while no sync is running)
        self.index_progress = QProgressBar()
        self.index_progress.setObjectName("IndexProgress")
        self.index_progress.setFixedHeight(18)
        self.index_progress.setVisible(False)
        layout.addWidget(self.index_progress)

        results_header = QHBoxLayout()
        results_header.setContentsMargins(5, 6, 5, 0)

        self.results_label = QLabel("Results: 0")
        self.results_label.setObjectName("ResultsHeader")
        results_header.addWidget(self.results_label)
        results_header.addStretch()

        help_label = QLabel("Enter / double-click to open  •  Right-click for more actions")
        help_label.setObjectName("HelpLabel")
        results_header.addWidget(help_label)
        layout.addLayout(results_header)

        self.result_list = ResultListWidget()
        self.result_list.itemDoubleClicked.connect(self.open_file)
        self.result_list.open_item_requested.connect(self.open_file)
        self.result_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.result_list.customContextMenuRequested.connect(self._show_result_context_menu)
        self.result_list.setMinimumHeight(380)
        layout.addWidget(self.result_list)

        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet(STATUS_STYLES["idle"])
        layout.addWidget(self.status_label)

        self.setLayout(layout)
        self.search_input.setFocus()

        self._setup_shortcuts()

    def _setup_shortcuts(self):
        focus_shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        focus_shortcut.activated.connect(self.search_input.setFocus)

        escape_shortcut = QShortcut(QKeySequence(Qt.Key_Escape), self)
        escape_shortcut.activated.connect(self.clear_search)

        # Ctrl+Enter: show the selected result in its folder
        for key_sequence in ("Ctrl+Return", "Ctrl+Enter"):
            reveal_shortcut = QShortcut(QKeySequence(key_sequence), self)
            reveal_shortcut.activated.connect(self._reveal_selected_result)

        # Ctrl+C while the result list has focus: copy the selected file path
        copy_shortcut = QShortcut(QKeySequence(QKeySequence.Copy), self.result_list)
        copy_shortcut.setContext(Qt.WidgetShortcut)
        copy_shortcut.activated.connect(self._copy_selected_result_path)

    def _set_status(self, text, kind="idle"):
        """Shows `text` in the status bar using one of the STATUS_STYLES colours."""
        self.status_label.setText(text)
        self.status_label.setStyleSheet(STATUS_STYLES[kind])

    def _current_query(self):
        """The full query sent to the engine: typed text + selected filter pill."""
        return build_search_query(self.search_input.text(), self.filter_bar.current_category())

    def _on_text_changed(self, text):
        if text.strip():
            self.search_timer.start(150)

    def _execute_search(self):
        self.perform_search()

    def _on_category_changed(self, _category_key):
        if self._current_query():
            self.perform_search()
        else:
            # "All" selected with an empty search box: nothing to show
            self.result_list.clear()
            self.results_label.setText("Results: 0")
            self._set_status("Ready", "idle")

    def trigger_incremental_sync(self):
        if self.indexing_thread and self.indexing_thread.isRunning():
            return
        self.sync_button.setEnabled(False)
        self.index_progress.setRange(0, 0)  # "busy" animation until counts arrive
        self.index_progress.setVisible(True)

        engine_arg = (
            self.index_data if isinstance(self.index_data, SmartLexEngine) else None
        )
        self.indexing_thread = IndexingThread(self, engine=engine_arg)
        self.indexing_thread.progress.connect(self._on_indexing_progress)

        def on_tier_ready(live_engine, live_words):
            self.update_index(live_engine, live_words, quiet=True)
            if self._current_query():
                self.perform_search()

        def on_sync_finished(new_engine, new_words):
            self.update_index(new_engine, new_words, quiet=False)
            self._start_realtime_watcher()

        self.indexing_thread.tier_ready_signal.connect(on_tier_ready)
        self.indexing_thread.finished_signal.connect(on_sync_finished)
        # QThread's built-in `finished` fires after run() returns for ANY reason
        # (success, error, or cancel) and after all of its other signals.
        self.indexing_thread.finished.connect(self._on_indexing_thread_stopped)
        self.indexing_thread.start()

    def _on_indexing_progress(self, message):
        self.status_label.setText(message)
        progress = parse_indexing_progress(message)
        if progress is None:
            # Scanning or saving: no file count available, keep the busy animation
            self.index_progress.setRange(0, 0)
            return
        files_done, files_total = progress
        self.index_progress.setRange(0, files_total)
        self.index_progress.setValue(files_done)
        label = message.split(" (")[0].rstrip(".")
        self.index_progress.setFormat(f"{label} (%p%)")

    def _on_indexing_thread_stopped(self):
        self.sync_button.setEnabled(True)
        self.index_progress.setVisible(False)

    def perform_search(self):
        query = self._current_query()

        if not query:
            self._set_status("Please enter a search query", "error")
            return

        try:
            self._set_status(f"Searching for: {query}...", "info")
            self.search_button.setEnabled(False)

            t0 = time.perf_counter()
            detailed_results = search_with_snippets(query, self.index_data)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0

            self.result_list.clear()

            if not detailed_results:
                paths = search(query, self.index_data)
                detailed_results = [{"path": p, "snippet": ""} for p in paths]

            if not detailed_results:
                self.result_list.add_message(
                    "No results found. Try different keywords or another filter."
                )
                self.results_label.setText("Results: 0")
                self._set_status(
                    f"No results found for '{query}' ({elapsed_ms:.2f} ms)", "warning"
                )
            else:
                for result in detailed_results:
                    self.result_list.add_result_card(result)

                self.result_list.setCurrentRow(0)
                self.results_label.setText(f"Results: {len(detailed_results)}")
                self._set_status(
                    f"Found {len(detailed_results)} result(s) for '{query}' in {elapsed_ms:.2f} ms",
                    "success",
                )

                logger.info(
                    f"Search query '{query}' returned {len(detailed_results)} results in {elapsed_ms:.2f}ms"
                )

        except Exception as e:
            error_msg = f"Search error: {str(e)}"
            logger.error(error_msg)
            self._set_status(error_msg, "error")
            QMessageBox.warning(
                self, "Search Error", f"An error occurred during search:\n{str(e)}"
            )

        finally:
            self.search_button.setEnabled(True)

    def clear_search(self):
        self.search_timer.stop()
        self.search_input.clear()
        self.filter_bar.reset()
        self.result_list.clear()
        self.results_label.setText("Results: 0")
        self._set_status("Ready", "idle")
        self.search_input.setFocus()

    # Result actions: open, show in folder, copy path

    def open_file(self, item):
        file_path = item.data(Qt.UserRole) if item is not None else None
        if file_path:
            self._open_result_path(file_path)

    def _show_result_context_menu(self, position):
        # For list widgets, `position` is in viewport coordinates
        item = self.result_list.itemAt(position)
        file_path = item.data(Qt.UserRole) if item is not None else None
        if not file_path:
            return
        self.result_list.setCurrentItem(item)

        menu = QMenu(self)
        open_action = menu.addAction("Open File")
        reveal_action = menu.addAction("Show in Folder")
        menu.addSeparator()
        copy_action = menu.addAction("Copy Full Path")

        chosen_action = menu.exec_(self.result_list.viewport().mapToGlobal(position))
        if chosen_action is open_action:
            self._open_result_path(file_path)
        elif chosen_action is reveal_action:
            self._reveal_result_path(file_path)
        elif chosen_action is copy_action:
            self._copy_result_path(file_path)

    def _reveal_selected_result(self):
        file_path = self.result_list.selected_file_path()
        if file_path:
            self._reveal_result_path(file_path)

    def _copy_selected_result_path(self):
        file_path = self.result_list.selected_file_path()
        if file_path:
            self._copy_result_path(file_path)

    def _warn_if_missing(self, file_path):
        """Shows a warning and returns True if `file_path` no longer exists."""
        if Path(file_path).exists():
            return False
        QMessageBox.warning(
            self,
            "File Not Found",
            f"The file does not exist:\n{file_path}\n\nIt may have been moved or deleted.",
        )
        logger.warning(f"Attempted to open non-existent file: {file_path}")
        return True

    def _open_result_path(self, file_path):
        if self._warn_if_missing(file_path):
            return
        try:
            open_file_with_default_app(file_path)
            self._set_status(f"Opened: {Path(file_path).name}", "success")
            logger.info(f"Opened file: {file_path}")
        except Exception as e:
            error_msg = f"Error opening file: {str(e)}"
            logger.error(f"{error_msg} - {file_path}")
            QMessageBox.critical(
                self,
                "Error Opening File",
                f"Could not open the file:\n{file_path}\n\nError: {str(e)}",
            )
            self._set_status(error_msg, "error")

    def _reveal_result_path(self, file_path):
        if self._warn_if_missing(file_path):
            return
        try:
            reveal_in_file_manager(file_path)
            self._set_status(f"Showing in folder: {Path(file_path).name}", "success")
        except Exception as e:
            error_msg = f"Error opening folder: {str(e)}"
            logger.error(f"{error_msg} - {file_path}")
            self._set_status(error_msg, "error")

    def _copy_result_path(self, file_path):
        QApplication.clipboard().setText(file_path)
        self._set_status(f"Copied path: {file_path}", "success")

    def keyPressEvent(self, event):
        key = event.key()

        if key in (Qt.Key_Return, Qt.Key_Enter):
            if self.search_input.hasFocus():
                self.perform_search()
            elif self.result_list.hasFocus():
                self.open_file(self.result_list.currentItem())

        elif key == Qt.Key_Tab:
            event.accept()
            if self.search_input.hasFocus():
                if self.result_list.count() > 0:
                    self.result_list.setFocus()
                    self.result_list.setCurrentRow(0)
            else:
                self.search_input.setFocus()

        elif key == Qt.Key_Down and self.search_input.hasFocus():
            if self.result_list.count() > 0:
                self.result_list.setFocus()
                self.result_list.setCurrentRow(0)

        else:
            super().keyPressEvent(event)

    def update_index(self, new_index_data, new_autocomplete_words, quiet=False):
        self.index_data = new_index_data
        self.autocomplete_words = new_autocomplete_words

        completer = CustomCompleter(new_autocomplete_words, self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setCompletionMode(completer.PopupCompletion)
        self.search_input.setCompleter(completer)

        if not quiet:
            self._set_status(
                f"Ready ({len(new_index_data)} files indexed • Live watcher active)",
                "success",
            )
        logger.info("Index and autocomplete data updated")
