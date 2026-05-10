import json
import os
import sys
from datetime import datetime

from PyQt5.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from smartlex.core.config import load_config
from smartlex.core.index_manager import generate_autocomplete, load_index, save_index
from smartlex.core.logger import setup_logger
from smartlex.gui.main_window import MyWidget
from smartlex.gui.threads import IndexingThread

logger = setup_logger("main")

def ensure_nltk_data():
    import nltk
    try:
        nltk.data.find('tokenizers/punkt')
    except LookupError:
        logger.info("Downloading NLTK punkt data...")
        nltk.download('punkt', quiet=True)
    
    try:
        nltk.data.find('corpora/stopwords')
    except LookupError:
        logger.info("Downloading NLTK stopwords data...")
        nltk.download('stopwords', quiet=True)

def main():
    app = QApplication(sys.argv)
    cfg = load_config()

    start = datetime.now()
    logger.info("Starting Lexical Search Engine")
    ensure_nltk_data()

    if os.path.exists(cfg["OUTPUT_FILE"]) and os.path.exists(cfg["AUTOCOMPLETE_FILE"]):
        D = load_index(cfg["OUTPUT_FILE"])
        with open(cfg["AUTOCOMPLETE_FILE"], "r") as f:
            autocomplete_words = json.load(f)

        widget = MyWidget(autocomplete_words, D)
        widget.show()
        app.main_window = widget

    else:
        loading = QWidget()
        loading.setWindowTitle("Indexing Documents...")
        layout = QVBoxLayout()
        label = QLabel("Indexing in progress...")
        layout.addWidget(label)
        loading.setLayout(layout)
        loading.show()

        thread = IndexingThread()
        thread.progress.connect(label.setText)

        def complete(D, words):
            save_index(D, cfg["OUTPUT_FILE"])
            loading.close()
            # Store reference in app to prevent garbage collection
            app.main_window = MyWidget(words, D)
            app.main_window.show()

        thread.finished_signal.connect(complete)
        thread.start()
        # prevent thread from being garbage collected
        app.indexing_thread = thread

    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
