import sys
from pathlib import Path

# Ensure `src/` is on sys.path so `python3 run.py` works out-of-the-box
# without requiring `PYTHONPATH=src` or `pip install -e .`
SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from smartlex.main import main  # noqa: E402

if __name__ == "__main__":
    main()
