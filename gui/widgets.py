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

    def splitPath(self, path):
        """
        Split the input path to get the current word being typed.
        
        This method is called by QCompleter to determine what to complete.
        We override it to only complete the current word, not the entire input.
        
        Args:
            path: The current text in the input field
            
        Returns:
            List containing words that match the current partial word
        """
        # Get text up to cursor position
        widget = self.widget()
        if not widget:
            return []

        text = widget.text()[:widget.cursorPosition()]
        
        if not text:
            return []

        # Split by whitespace to get individual words
        words = text.split()
        
        if not words:
            return []

        # Get the last word being typed
        current_word = words[-1].lower()
        
        # Return matching words from our word list
        matches = [word for word in self.words if word.lower().startswith(current_word)]
        
        return matches if matches else []

    def pathFromIndex(self, index):
        """
        Convert a completion index to the text that should replace the current word.
        
        Args:
            index: QModelIndex of the selected completion
            
        Returns:
            The completed word
        """
        return super().pathFromIndex(index)

    def updateModel(self):
        """Update the completion model with current matches."""
        widget = self.widget()
        if not widget:
            return

        text = widget.text()[:widget.cursorPosition()]
        
        if not text:
            self.setModel(self.model())
            return

        words = text.split()
        if not words:
            return

        current_word = words[-1].lower()
        
        # Filter words that start with the current word
        matches = [word for word in self.words if word.lower().startswith(current_word)]
        
        # Update the model with filtered matches
        from PyQt5.QtCore import QStringListModel
        self.setModel(QStringListModel(matches))

    def insertText(self, completion):
        """
        Insert the completed text, replacing only the current word.
        
        Args:
            completion: The completed word to insert
        """
        widget = self.widget()
        if not widget:
            return

        text = widget.text()
        cursor_pos = widget.cursorPosition()
        
        # Get text before cursor
        text_before = text[:cursor_pos]
        text_after = text[cursor_pos:]
        
        # Split to find where the current word starts
        words_before = text_before.split()
        
        if not words_before:
            # If no words before cursor, just insert the completion
            widget.setText(completion + " " + text_after.lstrip())
            widget.setCursorPosition(len(completion) + 1)
            return

        # Find the start position of the last word
        last_word = words_before[-1]
        word_start = text_before.rfind(last_word)
        
        # Replace the last word with the completion
        new_text = text_before[:word_start] + completion + " " + text_after.lstrip()
        widget.setText(new_text)
        
        # Set cursor after the completed word and space
        new_cursor_pos = word_start + len(completion) + 1
        widget.setCursorPosition(new_cursor_pos)


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