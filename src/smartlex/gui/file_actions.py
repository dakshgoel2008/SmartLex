"""Cross-platform "open file" and "show in folder" commands, kept Qt-free for testing."""

import os
import subprocess
import sys
from pathlib import Path
from typing import List


def build_open_command(file_path: str, platform: str = sys.platform) -> List[str]:
    """
    Returns the command that opens `file_path` with its default application
    on macOS and Linux. (Windows uses `os.startfile` instead, see below.)
    """
    if platform == "darwin":
        return ["open", file_path]
    return ["xdg-open", file_path]


def build_reveal_command(file_path: str, platform: str = sys.platform) -> List[str]:
    """Returns the command that shows `file_path` in the system file manager."""
    if platform == "win32":
        # "/select," and the path are passed as separate arguments so paths
        # containing spaces are quoted correctly by subprocess.
        return ["explorer", "/select,", os.path.normpath(file_path)]
    if platform == "darwin":
        return ["open", "-R", file_path]
    return ["xdg-open", str(Path(file_path).parent)]


def open_file_with_default_app(file_path: str) -> None:
    """Opens a file with the operating system's default application."""
    if sys.platform == "win32":
        os.startfile(file_path)  # type: ignore[attr-defined]  (Windows-only API)
    else:
        subprocess.Popen(build_open_command(file_path))


def reveal_in_file_manager(file_path: str) -> None:
    """Shows a file in the operating system's file manager."""
    subprocess.Popen(build_reveal_command(file_path))

