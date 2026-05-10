from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QCompleter


class CustomCompleter(QCompleter):
    """
    Custom completer that completes individual words in a multi-word search query.
    
    Instead of replacing the entire line, this completer only replaces the current
    word being typed, allowing for natural multi-keyword searches.
    """

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
    """
    Completer for search history that shows previous complete queries.
    
    This completer can be used alongside CustomCompleter to provide
    full query history suggestions.
    """

    def __init__(self, history, parent=None):
        """
        Initialize the search history completer.
        
        Args:
            history: List of previous search queries
            parent: Parent widget (optional)
        """
        super().__init__(history, parent)
        self.history = list(reversed(history))  # Most recent first
        self.setCaseSensitivity(Qt.CaseInsensitive)
        self.setCompletionMode(QCompleter.UnfilteredPopupCompletion)
        self.setMaxVisibleItems(8)

    def update_history(self, new_query):
        """
        Add a new query to the history.
        
        Args:
            new_query: The search query to add to history
        """
        if new_query and new_query not in self.history:
            self.history.insert(0, new_query)
            # Keep only last 100 queries
            self.history = self.history[:100]
            
            from PyQt5.QtCore import QStringListModel
            self.setModel(QStringListModel(self.history))

    def get_history(self):
        """
        Get the current search history.
        
        Returns:
            List of search queries in chronological order (most recent first)
        """
        return self.history.copy()