"""Shared frozen entry point for the game and extractor executables."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _bundle_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def _ensure_console_streams() -> None:
    """Give libraries harmless streams in PyInstaller windowed executables."""

    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def _diagnostics() -> int:
    from opendance.extract import diagnostics

    return diagnostics()


def main() -> int:
    _ensure_console_streams()
    model = _bundle_root() / "models" / "yolo26n-pose.pt"
    if model.is_file():
        os.environ.setdefault("OPENDANCE_MODEL", str(model))

    if "--diagnostics" in sys.argv:
        return _diagnostics()
    if Path(sys.executable).stem.casefold().startswith("opendance-extract"):
        from opendance.extract import main as extract

        return extract()
    if Path(sys.executable).stem.casefold().startswith("opendance-player"):
        from opendance.player import main as player

        return player()

    from opendance.app import main as game

    return game()


if __name__ == "__main__":
    raise SystemExit(main())
