import logging
from pathlib import Path


def _resolve_log_dir() -> Path:
    """
    Uses `./logs` when running inside the project repository, or falls back to
    `~/.smartlex/logs` when launched globally as a PyPI package.
    """
    local_logs = Path("logs")
    if local_logs.is_dir() or Path("config.json").is_file():
        try:
            local_logs.mkdir(parents=True, exist_ok=True)
            return local_logs
        except OSError:
            pass

    user_logs = Path.home() / ".smartlex" / "logs"
    user_logs.mkdir(parents=True, exist_ok=True)
    return user_logs


def setup_logger(name: str = "search_engine", log_file: str = "search_engine.log") -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

        try:
            log_path = _resolve_log_dir()
            fh = logging.FileHandler(log_path / log_file, encoding="utf-8")
            fh.setFormatter(formatter)
            logger.addHandler(fh)
        except OSError:
            pass

        sh = logging.StreamHandler()
        sh.setFormatter(formatter)
        logger.addHandler(sh)

    return logger
