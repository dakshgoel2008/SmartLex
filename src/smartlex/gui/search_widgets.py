"""
Phase 4 widgets for the SmartLex search window:

  - FilterPillBar:    a row of "All / Docs / Sheets / ..." toggle pills.
  - ResultCard:       one rich search result (icon, name, folder, highlighted snippet, details).
  - ResultListWidget: a QListWidget that hosts ResultCards and keeps their heights correct.

All decisions about *what* to display come from `result_formatting.py`; this
module only lays it out with Qt.
"""

from typing import Any, Dict, Optional

from PyQt5.QtCore import QSize, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from smartlex.gui.result_formatting import (
    FILTER_CATEGORIES,
    describe_file,
    highlight_snippet_html,
)


class FilterPillBar(QWidget):
    """
    A row of exclusive toggle buttons, one per file category.
    Emits `category_changed(category_key)` when the user picks a pill
    ("" means "All").
    """

    category_changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._button_group = QButtonGroup(self)
        self._button_group.setExclusive(True)
        self._buttons_by_key: Dict[str, QPushButton] = {}

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        for category in FILTER_CATEGORIES:
            button = QPushButton(f"{category.icon}  {category.label}")
            button.setObjectName("FilterPill")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setFocusPolicy(Qt.NoFocus)  # keep keyboard focus in the search box
            button.setProperty("category_key", category.key)

            self._button_group.addButton(button)
            self._buttons_by_key[category.key] = button
            layout.addWidget(button)

        layout.addStretch()
        self._buttons_by_key[""].setChecked(True)
        self._button_group.buttonClicked.connect(self._on_button_clicked)

    def current_category(self) -> str:
        """Returns the selected category key ("" for All)."""
        checked_button = self._button_group.checkedButton()
        if checked_button is None:
            return ""
        return checked_button.property("category_key") or ""

    def reset(self) -> None:
        """Selects the "All" pill without emitting `category_changed`."""
        self._buttons_by_key[""].setChecked(True)

    def _on_button_clicked(self, button: QPushButton) -> None:
        self.category_changed.emit(button.property("category_key") or "")


class ResultCard(QFrame):
    """
    Displays one search result:

        📄  invoice_2024.pdf
            /home/user/Documents/Finance
            ...the total due for invoice 2024 is $1,250...
            PDF Document  •  1.4 MB  •  Modified 16 Oct 2026, 21:24
    """

    def __init__(self, result: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.setObjectName("ResultCard")
        self.file_path: str = result["path"]
        details = describe_file(self.file_path)

        icon_label = QLabel(details.icon)
        icon_label.setObjectName("CardIcon")
        icon_label.setFixedWidth(40)
        icon_label.setAlignment(Qt.AlignHCenter | Qt.AlignTop)

        # File names and folders are shown as PLAIN text so a file called
        # "<b>x</b>.txt" is displayed literally instead of being rendered.
        name_label = self._make_plain_label(details.name, "CardTitle")
        folder_label = self._make_plain_label(details.folder, "CardPath")
        folder_label.setToolTip(self.file_path)

        text_column = QVBoxLayout()
        text_column.setSpacing(3)
        text_column.addWidget(name_label)
        text_column.addWidget(folder_label)

        snippet_html = highlight_snippet_html(
            result.get("snippet", ""), result.get("matched_words", [])
        )
        if snippet_html:
            snippet_label = QLabel(snippet_html)
            snippet_label.setObjectName("CardSnippet")
            snippet_label.setTextFormat(Qt.RichText)
            snippet_label.setWordWrap(True)
            text_column.addWidget(snippet_label)

        meta_label = self._make_plain_label(self._build_meta_text(details), "CardMeta")
        text_column.addWidget(meta_label)

        card_layout = QHBoxLayout(self)
        card_layout.setContentsMargins(10, 10, 10, 10)
        card_layout.setSpacing(10)
        card_layout.addWidget(icon_label)
        card_layout.addLayout(text_column, stretch=1)

    @staticmethod
    def _make_plain_label(text: str, object_name: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName(object_name)
        label.setTextFormat(Qt.PlainText)
        # Let long single-line text be clipped instead of widening the whole list
        label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        label.setMinimumWidth(1)
        return label

    @staticmethod
    def _build_meta_text(details) -> str:
        if not details.exists:
            return f"{details.type_name}  •  File no longer exists"
        return (
            f"{details.type_name}  •  {details.size_text}  •  "
            f"Modified {details.modified_text}"
        )


class ResultListWidget(QListWidget):
    """
    A QListWidget whose rows are ResultCards.

    Cards contain word-wrapped snippets, so their height depends on the list
    width. `refresh_card_heights()` recalculates every row height and is
    called after results are added and whenever the list is resized.
    """

    open_item_requested = pyqtSignal(QListWidgetItem)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.setSelectionMode(QListWidget.SingleSelection)

    def add_result_card(self, result: Dict[str, Any]) -> QListWidgetItem:
        """Appends a ResultCard row and stores its file path on the item."""
        item = QListWidgetItem(self)
        item.setData(Qt.UserRole, result["path"])
        card = ResultCard(result, self.viewport())
        self.setItemWidget(item, card)
        self._update_item_height(item)
        return item

    def add_message(self, message: str) -> QListWidgetItem:
        """Appends a plain, non-openable message row (e.g. "No results found")."""
        item = QListWidgetItem(message, self)
        item.setFlags(Qt.NoItemFlags)
        return item

    def selected_file_path(self) -> Optional[str]:
        """Returns the file path of the selected result card, if any."""
        item = self.currentItem()
        if item is None:
            return None
        return item.data(Qt.UserRole)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            current = self.currentItem()
            if current is not None and current.data(Qt.UserRole):
                event.accept()
                self.open_item_requested.emit(current)
                return
        super().keyPressEvent(event)

    def refresh_card_heights(self) -> None:
        for row in range(self.count()):
            self._update_item_height(self.item(row))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.refresh_card_heights()

    def _update_item_height(self, item: QListWidgetItem) -> None:
        card = self.itemWidget(item)
        if card is None:
            return
        # Ensure stylesheet font sizes are applied before measuring height
        card.ensurePolished()
        for child in card.findChildren(QWidget):
            child.ensurePolished()
        if card.layout() is not None:
            card.layout().activate()

        # Always reserve room for the vertical scrollbar so a row never ends up
        # one line too short when the scrollbar appears after results load.
        card_width = max(1, self.viewport().width() - self.verticalScrollBar().sizeHint().width())
        hfw = card.heightForWidth(card_width) if card.hasHeightForWidth() else -1
        card_height = max(
            hfw,
            card.sizeHint().height(),
            card.minimumSizeHint().height(),
        )
        item.setSizeHint(QSize(card_width, card_height))

