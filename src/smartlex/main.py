import json
import os
import sys

from PyQt5.QtWidgets import QApplication

from smartlex.core.config import load_config
from smartlex.core.index_manager import load_index
from smartlex.core.logger import setup_logger
from smartlex.core.native_engine import SmartLexEngine
from smartlex.gui.main_window import MyWidget

logger = setup_logger("main")


def main():
    app = QApplication(sys.argv)
    cfg = load_config()

    logger.info("Starting SmartLex Search Engine (Phase 2 Zero-Wait Core)")

    index_file = cfg.get("INDEX_FILE", cfg["OUTPUT_FILE"])
    legacy_file = cfg["OUTPUT_FILE"]
    catalog_file = cfg.get("CATALOG_FILE", "smartlex_catalog.db")
    has_index = os.path.exists(index_file) or os.path.exists(legacy_file)

    if has_index and os.path.exists(cfg["AUTOCOMPLETE_FILE"]):
        engine = load_index(
            index_file if os.path.exists(index_file) else legacy_file,
            catalog_file,
        )
        with open(cfg["AUTOCOMPLETE_FILE"], "r", encoding="utf-8") as f:
            autocomplete_words = json.load(f)

        # Open UI immediately and run a non-blocking background delta check
        widget = MyWidget(autocomplete_words, engine, start_sync_immediately=True)
        widget.show()
        app.main_window = widget

    else:
        # Zero-Wait First Launch: Open the main search window IMMEDIATELY
        # while Tier-0 (instant filenames) -> Tier-1 (content micro-batches) stream in!
        engine = SmartLexEngine(index_file=index_file, catalog_file=catalog_file)
        widget = MyWidget([], engine, start_sync_immediately=True)
        widget.show()
        app.main_window = widget

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
