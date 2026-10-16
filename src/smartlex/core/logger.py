import logging
from pathlib import Path


def setup_logger(name: str = "search_engine", log_file: str = "search_engine.log") -> logging.Logger:
    log_path = Path("logs")
    log_path.mkdir(exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

        fh = logging.FileHandler(log_path / log_file, encoding="utf-8")
        sh = logging.StreamHandler()

        fh.setFormatter(formatter)
        sh.setFormatter(formatter)

        logger.addHandler(fh)
        logger.addHandler(sh)

    return logger
