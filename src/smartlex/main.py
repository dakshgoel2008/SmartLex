import json
import os
import sys

from PyQt5.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from smartlex.core.config import load_config
from smartlex.core.index_manager import load_index
from smartlex.core.logger import setup_logger
from smartlex.gui.main_window import MyWidget
from smartlex.gui.threads import IndexingThread

logger = setup_logger("main")


def main():
    app = QApplication(sys.argv)
    cfg = load_config()

    logger.info("Starting SmartLex Search Engine (Phase 1 C++20 Core)")

    index_file = cfg.get("INDEX_FILE", cfg["OUTPUT_FILE"])
    legacy_file = cfg["OUTPUT_FILE"]
    has_index = os.path.exists(index_file) or os.path.exists(legacy_file)

    if has_index and os.path.exists(cfg["AUTOCOMPLETE_FILE"]):
        engine = load_index(
            index_file if os.path.exists(index_file) else legacy_file,
            cfg.get("CATALOG_FILE", "smartlex_catalog.db"),
        )
        with open(cfg["AUTOCOMPLETE_FILE"], "r", encoding="utf-8") as f:
            autocomplete_words = json.load(f)

        widget = MyWidget(autocomplete_words, engine)
        widget.show()
        app.main_window = widget

    else:
        loading = QWidget()
        loading.setWindowTitle("Indexing Documents...")
        loading.resize(450, 120)
        layout = QVBoxLayout()
        label = QLabel("Indexing in progress...")
        layout.addWidget(label)
        loading.setLayout(layout)
        loading.show()

        thread = IndexingThread()
        thread.progress.connect(label.setText)

        def complete(engine, words):
            loading.close()
            app.main_window = MyWidget(words, engine)
            app.main_window.show()

        thread.finished_signal.connect(complete)
        thread.start()
        app.indexing_thread = thread

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
