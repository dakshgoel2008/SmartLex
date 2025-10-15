import json
import os
import sys
from datetime import datetime

from PyQt5.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from core.config import load_config
from core.index_manager import generate_autocomplete, load_index, save_index
from core.logger import setup_logger
from gui.main_window import MyWidget
from gui.threads import IndexingThread

logger = setup_logger("main")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    cfg = load_config()

    start = datetime.now()
    logger.info("Starting Lexical Search Engine")

    if os.path.exists(cfg["OUTPUT_FILE"]):
        D = load_index(cfg["OUTPUT_FILE"])
        with open(cfg["AUTOCOMPLETE_FILE"], "r") as f:
            autocomplete_words = json.load(f)

        widget = MyWidget(autocomplete_words, D)
        widget.show()

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
            widget = MyWidget(words, D)
            widget.show()

        thread.finished_signal.connect(complete)
        thread.start()

    sys.exit(app.exec_())
