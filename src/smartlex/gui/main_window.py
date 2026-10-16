import subprocess
import sys
import time
from pathlib import Path

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QIcon, QKeySequence
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
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
from smartlex.gui.threads import IndexingThread
from smartlex.gui.widgets import CustomCompleter

logger = setup_logger(__name__)


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
            if self.search_input.text().strip():
                self.perform_search()

    def closeEvent(self, event):
        if self.file_watcher is not None:
            self.file_watcher.stop()
        if self.indexing_thread is not None and self.indexing_thread.isRunning():
            self.indexing_thread.cancel()
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
                margin-bottom: 25px;
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
            QListWidget {
                border: 2px solid #e0e6ed;
                border-radius: 12px;
                background-color: #ffffff;
                font-size: 13pt;
                padding: 10px;
                outline: none;
            }
            QListWidget::item {
                padding: 15px;
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
                font-weight: 600;
            }
            QListView::item {
                padding: 8px;
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
        layout.setSpacing(15)
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
            'Search keywords, "exact phrase", ext:pdf, path:docs, -exclude (e.g. invoice 2024)'
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

        results_header = QHBoxLayout()
        results_header.setContentsMargins(5, 10, 5, 0)

        self.results_label = QLabel("Results: 0")
        self.results_label.setObjectName("ResultsHeader")
        results_header.addWidget(self.results_label)
        results_header.addStretch()

        help_label = QLabel("Double-click or press Enter to open file")
        help_label.setObjectName("HelpLabel")
        results_header.addWidget(help_label)
        layout.addLayout(results_header)

        self.result_list = QListWidget()
        self.result_list.setAlternatingRowColors(True)
        self.result_list.itemDoubleClicked.connect(self.open_file)
        self.result_list.setMinimumHeight(400)
        layout.addWidget(self.result_list)

        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet(
            "padding: 10px; background-color: #ffffff; border-radius: 8px; border: 1px solid #e0e6ed; color: #7f8c8d; font-weight: bold;"
        )
        layout.addWidget(self.status_label)

        self.setLayout(layout)
        self.search_input.setFocus()

        self._setup_shortcuts()

    def _setup_shortcuts(self):
        focus_shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        focus_shortcut.activated.connect(self.search_input.setFocus)

        escape_shortcut = QShortcut(QKeySequence(Qt.Key_Escape), self)
        escape_shortcut.activated.connect(self.clear_search)

    def _on_text_changed(self, text):
        if text.strip():
            self.search_timer.start(150)

    def _execute_search(self):
        self.perform_search()

    def trigger_incremental_sync(self):
        if self.indexing_thread and self.indexing_thread.isRunning():
            return
        self.sync_button.setEnabled(False)
        engine_arg = (
            self.index_data if isinstance(self.index_data, SmartLexEngine) else None
        )
        self.indexing_thread = IndexingThread(self, engine=engine_arg)
        self.indexing_thread.progress.connect(self.status_label.setText)

        def on_tier_ready(live_engine, live_words):
            self.update_index(live_engine, live_words, quiet=True)
            if self.search_input.text().strip():
                self.perform_search()

        def on_sync_finished(new_engine, new_words):
            self.sync_button.setEnabled(True)
            self.update_index(new_engine, new_words, quiet=False)
            self._start_realtime_watcher()

        def on_sync_error(_msg):
            self.sync_button.setEnabled(True)

        self.indexing_thread.tier_ready_signal.connect(on_tier_ready)
        self.indexing_thread.finished_signal.connect(on_sync_finished)
        self.indexing_thread.error_signal.connect(on_sync_error)
        self.indexing_thread.start()

    def perform_search(self):
        query = self.search_input.text().strip()

        if not query:
            self.status_label.setText("Please enter a search query")
            self.status_label.setStyleSheet(
                "padding: 10px; background-color: #fdf2f2; border-radius: 8px; border: 1px solid #fecaca; color: #ef4444; font-weight: bold;"
            )
            return

        try:
            self.status_label.setText(f"Searching for: {query}...")
            self.status_label.setStyleSheet(
                "padding: 10px; background-color: #f0f7ff; border-radius: 8px; border: 1px solid #bae6fd; color: #0284c7; font-weight: bold;"
            )
            self.search_button.setEnabled(False)

            t0 = time.perf_counter()
            detailed_results = search_with_snippets(query, self.index_data)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0

            self.result_list.clear()

            if not detailed_results:
                paths = search(query, self.index_data)
                detailed_results = [{"path": p, "snippet": ""} for p in paths]

            if not detailed_results:
                self.result_list.addItem("No results found. Try different keywords.")
                self.results_label.setText("Results: 0")
                self.status_label.setText(
                    f"No results found for '{query}' ({elapsed_ms:.2f} ms)"
                )
                self.status_label.setStyleSheet(
                    "padding: 10px; background-color: #fefce8; border-radius: 8px; border: 1px solid #fef08a; color: #a16207; font-weight: bold;"
                )
            else:
                for res in detailed_results:
                    file_path = res["path"]
                    snippet = res.get("snippet", "")
                    display_text = (
                        f"{file_path}\n    {snippet}" if snippet else file_path
                    )

                    item = QListWidgetItem(display_text)
                    item.setData(Qt.UserRole, file_path)
                    if snippet:
                        item.setToolTip(snippet)
                    self.result_list.addItem(item)

                self.result_list.setCurrentRow(0)
                self.results_label.setText(f"Results: {len(detailed_results)}")
                self.status_label.setText(
                    f"Found {len(detailed_results)} result(s) for '{query}' in {elapsed_ms:.2f} ms"
                )
                self.status_label.setStyleSheet(
                    "padding: 10px; background-color: #f0fdf4; border-radius: 8px; border: 1px solid #bbf7d0; color: #166534; font-weight: bold;"
                )

                logger.info(
                    f"Search query '{query}' returned {len(detailed_results)} results in {elapsed_ms:.2f}ms"
                )

        except Exception as e:
            error_msg = f"Search error: {str(e)}"
            logger.error(error_msg)
            self.status_label.setText(error_msg)
            self.status_label.setStyleSheet(
                "padding: 10px; background-color: #fdf2f2; border-radius: 8px; border: 1px solid #fecaca; color: #ef4444; font-weight: bold;"
            )
            QMessageBox.warning(
                self, "Search Error", f"An error occurred during search:\n{str(e)}"
            )

        finally:
            self.search_button.setEnabled(True)

    def clear_search(self):
        self.search_timer.stop()
        self.search_input.clear()
        self.result_list.clear()
        self.results_label.setText("Results: 0")
        self.status_label.setText("Ready")
        self.status_label.setStyleSheet(
            "padding: 10px; background-color: #ffffff; border-radius: 8px; border: 1px solid #e0e6ed; color: #7f8c8d; font-weight: bold;"
        )
        self.search_input.setFocus()

    def open_file(self, item):
        file_path = item.data(Qt.UserRole) or item.text().split("\n")[0]

        if file_path in (
            "No results found. Try different keywords.",
            "No results found",
        ):
            return

        file = Path(file_path)

        if not file.exists():
            QMessageBox.warning(
                self,
                "File Not Found",
                f"The file does not exist:\n{file_path}\n\nIt may have been moved or deleted.",
            )
            logger.warning(f"Attempted to open non-existent file: {file_path}")
            return

        try:
            if sys.platform == "win32":
                subprocess.Popen(["start", "", file_path], shell=True)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", file_path])
            else:
                subprocess.Popen(["xdg-open", file_path])

            self.status_label.setText(f"Opened: {file.name}")
            self.status_label.setStyleSheet(
                "padding: 10px; background-color: #f0fdf4; border-radius: 8px; border: 1px solid #bbf7d0; color: #166534; font-weight: bold;"
            )
            logger.info(f"Opened file: {file_path}")

        except Exception as e:
            error_msg = f"Error opening file: {str(e)}"
            logger.error(f"{error_msg} - {file_path}")
            QMessageBox.critical(
                self,
                "Error Opening File",
                f"Could not open the file:\n{file_path}\n\nError: {str(e)}",
            )
            self.status_label.setText(error_msg)
            self.status_label.setStyleSheet(
                "padding: 10px; background-color: #fdf2f2; border-radius: 8px; border: 1px solid #fecaca; color: #ef4444; font-weight: bold;"
            )

    def keyPressEvent(self, event):
        key = event.key()

        if key in (Qt.Key_Return, Qt.Key_Enter):
            if self.search_input.hasFocus():
                self.perform_search()
            elif self.result_list.hasFocus():
                items = self.result_list.selectedItems()
                if items:
                    self.open_file(items[0])

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
            self.status_label.setText(
                f"✓ Ready ({len(new_index_data)} files indexed • Live watcher active)"
            )
            self.status_label.setStyleSheet(
                "padding: 10px; background-color: #f0fdf4; border-radius: 8px; border: 1px solid #bbf7d0; color: #166534; font-weight: bold;"
            )
        logger.info("Index and autocomplete data updated")
