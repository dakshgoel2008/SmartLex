from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QCompleter


class CustomCompleter(QCompleter):
    """Completes only the word being typed, so multi-word queries keep their other words."""

    def __init__(self, words, parent=None):
        """
        Initialize the custom completer.
        """
        super().__init__(words, parent)
        self.words = sorted(set(words))  # Remove duplicates and sort
        self.setCaseSensitivity(Qt.CaseInsensitive)
        self.setCompletionMode(QCompleter.PopupCompletion)
        self.setMaxVisibleItems(10)

    def pathFromIndex(self, index):
        """
        Append the selected completion to the preceding words.
        """
        path = super().pathFromIndex(index)
        widget = self.widget()
        if widget:
            text = widget.text()
            words = text.split(' ')
            if len(words) > 1:
                path = ' '.join(words[:-1]) + ' ' + path
        return path

    def splitPath(self, path):
        """
        Filter completions based only on the last word being typed.
        """
        return [path.split(' ')[-1]]


class SearchHistoryCompleter(QCompleter):
    """Suggests previous complete queries."""

    def __init__(self, history, parent=None):
        super().__init__(history, parent)
        self.history = list(reversed(history))  # Most recent first
        self.setCaseSensitivity(Qt.CaseInsensitive)
        self.setCompletionMode(QCompleter.UnfilteredPopupCompletion)
        self.setMaxVisibleItems(8)

    def update_history(self, new_query):
        """Adds a query to the front of the history."""
        if new_query and new_query not in self.history:
            self.history.insert(0, new_query)
            # Keep only last 100 queries
            self.history = self.history[:100]
            
            from PyQt5.QtCore import QStringListModel
            self.setModel(QStringListModel(self.history))

    def get_history(self):
        """Returns the history, most recent first."""
        return self.history.copy()