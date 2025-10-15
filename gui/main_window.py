import subprocess
import sys
from pathlib import Path

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QIcon, QFont, QKeySequence
from PyQt5.QtWidgets import (
    QLineEdit, QListWidget, QPushButton, QVBoxLayout, 
    QWidget, QLabel, QHBoxLayout, QMessageBox, QShortcut
)

from core.logger import setup_logger
from core.search_engine import search
from gui.widgets import CustomCompleter

logger = setup_logger(__name__)


class MyWidget(QWidget):
    """Main GUI window for lexical search engine."""

    def __init__(self, autocomplete_words, index_data):
        super().__init__()
        self.index_data = index_data
        self.autocomplete_words = autocomplete_words
        self.search_timer = QTimer()
        self.search_timer.setSingleShot(True)
        self.search_timer.timeout.connect(self._execute_search)
        self.initUI()

    def initUI(self):
        """Initialize the user interface."""
        self.setWindowTitle("Lexical Search Engine")
        icon_path = Path("icon.png")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.setMinimumSize(900, 650)
        self.resize(1000, 700)

        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(15, 15, 15, 15)

        # Title label
        title_label = QLabel("Lexical Search Engine")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(title_label)

        # Search section
        search_layout = QHBoxLayout()
        
        # Search input with improved styling
        completer = CustomCompleter(self.autocomplete_words, self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setCompletionMode(completer.PopupCompletion)
        
        self.search_input = QLineEdit()
        self.search_input.setCompleter(completer)
        self.search_input.setPlaceholderText("Enter search keywords... (Press Tab to focus results)")
        self.search_input.setMinimumHeight(35)
        self.search_input.textChanged.connect(self._on_text_changed)
        search_layout.addWidget(self.search_input, stretch=4)

        # Search button with improved styling
        self.search_button = QPushButton("Search")
        self.search_button.setMinimumHeight(35)
        self.search_button.setMinimumWidth(100)
        self.search_button.clicked.connect(self.perform_search)
        self.search_button.setDefault(True)
        search_layout.addWidget(self.search_button)

        # Clear button
        self.clear_button = QPushButton("Clear")
        self.clear_button.setMinimumHeight(35)
        self.clear_button.setMinimumWidth(80)
        self.clear_button.clicked.connect(self.clear_search)
        search_layout.addWidget(self.clear_button)

        layout.addLayout(search_layout)

        # Results header
        results_header = QHBoxLayout()
        self.results_label = QLabel("Results: 0")
        results_header.addWidget(self.results_label)
        results_header.addStretch()
        
        help_label = QLabel("Double-click or press Enter to open file")
        help_label.setStyleSheet("color: gray; font-size: 10pt;")
        results_header.addWidget(help_label)
        layout.addLayout(results_header)

        # Result list with improved styling
        self.result_list = QListWidget()
        self.result_list.setAlternatingRowColors(True)
        self.result_list.itemDoubleClicked.connect(self.open_file)
        self.result_list.setMinimumHeight(400)
        layout.addWidget(self.result_list)

        # Status bar
        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet("padding: 5px; background-color: #f0f0f0; border-radius: 3px;")
        layout.addWidget(self.status_label)

        self.setLayout(layout)
        self.search_input.setFocus()

        # Add keyboard shortcuts
        self._setup_shortcuts()

    def _setup_shortcuts(self):
        """Setup keyboard shortcuts."""
        # Ctrl+F to focus search
        focus_shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        focus_shortcut.activated.connect(self.search_input.setFocus)
        
        # Escape to clear search
        escape_shortcut = QShortcut(QKeySequence(Qt.Key_Escape), self)
        escape_shortcut.activated.connect(self.clear_search)

    def _on_text_changed(self, text):
        """Handle text changes with debouncing for live search."""
        pass

    def _execute_search(self):
        """Execute the search operation."""
        self.perform_search()

    def perform_search(self):
        """Perform search based on current input."""
        query = self.search_input.text().strip()
        
        if not query:
            self.status_label.setText("Please enter a search query")
            self.status_label.setStyleSheet("padding: 5px; background-color: #ffe6e6; border-radius: 3px; color: #cc0000;")
            return

        try:
            self.status_label.setText(f"Searching for: {query}...")
            self.status_label.setStyleSheet("padding: 5px; background-color: #e6f3ff; border-radius: 3px; color: #0066cc;")
            self.search_button.setEnabled(False)
            
            # Perform search
            results = search(query, self.index_data)
            
            # Update results
            self.result_list.clear()
            
            if not results:
                self.result_list.addItem("No results found. Try different keywords.")
                self.results_label.setText("Results: 0")
                self.status_label.setText(f"No results found for '{query}'")
                self.status_label.setStyleSheet("padding: 5px; background-color: #fff3cd; border-radius: 3px; color: #856404;")
            else:
                for file in results:
                    self.result_list.addItem(file)
                
                self.result_list.setCurrentRow(0)
                self.results_label.setText(f"Results: {len(results)}")
                self.status_label.setText(f"Found {len(results)} result(s) for '{query}'")
                self.status_label.setStyleSheet("padding: 5px; background-color: #d4edda; border-radius: 3px; color: #155724;")
                
                logger.info(f"Search query '{query}' returned {len(results)} results")

        except Exception as e:
            error_msg = f"Search error: {str(e)}"
            logger.error(error_msg)
            self.status_label.setText(error_msg)
            self.status_label.setStyleSheet("padding: 5px; background-color: #ffe6e6; border-radius: 3px; color: #cc0000;")
            QMessageBox.warning(self, "Search Error", f"An error occurred during search:\n{str(e)}")
        
        finally:
            self.search_button.setEnabled(True)

    def clear_search(self):
        """Clear search input and results."""
        self.search_input.clear()
        self.result_list.clear()
        self.results_label.setText("Results: 0")
        self.status_label.setText("Ready")
        self.status_label.setStyleSheet("padding: 5px; background-color: #f0f0f0; border-radius: 3px;")
        self.search_input.setFocus()

    def open_file(self, item):
        """Open the selected file."""
        file_path = item.text()
        
        if file_path in ("No results found. Try different keywords.", "No results found"):
            return

        file = Path(file_path)
        
        if not file.exists():
            QMessageBox.warning(
                self, 
                "File Not Found", 
                f"The file does not exist:\n{file_path}\n\nIt may have been moved or deleted."
            )
            logger.warning(f"Attempted to open non-existent file: {file_path}")
            return

        try:
            if sys.platform == "win32":
                subprocess.Popen(['start', '', file_path], shell=True)
            elif sys.platform == "darwin":  # macOS
                subprocess.Popen(['open', file_path])
            else:  # Linux and others
                subprocess.Popen(['xdg-open', file_path])
            
            self.status_label.setText(f"Opened: {file.name}")
            self.status_label.setStyleSheet("padding: 5px; background-color: #d4edda; border-radius: 3px; color: #155724;")
            logger.info(f"Opened file: {file_path}")
            
        except Exception as e:
            error_msg = f"Error opening file: {str(e)}"
            logger.error(f"{error_msg} - {file_path}")
            QMessageBox.critical(
                self, 
                "Error Opening File", 
                f"Could not open the file:\n{file_path}\n\nError: {str(e)}"
            )
            self.status_label.setText(error_msg)
            self.status_label.setStyleSheet("padding: 5px; background-color: #ffe6e6; border-radius: 3px; color: #cc0000;")

    def keyPressEvent(self, event):
        """Handle keyboard events."""
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

    def update_index(self, new_index_data, new_autocomplete_words):
        """Update index data and autocomplete words."""
        self.index_data = new_index_data
        self.autocomplete_words = new_autocomplete_words
        
        # Update completer with new words
        completer = CustomCompleter(new_autocomplete_words, self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setCompletionMode(completer.PopupCompletion)
        self.search_input.setCompleter(completer)
        
        self.status_label.setText("Index updated successfully")
        self.status_label.setStyleSheet("padding: 5px; background-color: #d4edda; border-radius: 3px; color: #155724;")
        logger.info("Index and autocomplete data updated")