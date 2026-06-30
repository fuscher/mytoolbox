# MyToolbox Core Module

from __future__ import annotations

import sys
from pathlib import Path


def get_app_root() -> Path:
    """Return the root directory where config / tools / resources live.

    In PyInstaller frozen mode this is the directory containing the .exe.
    In dev mode it's the project root (two levels above this file).
    """
    if getattr(sys, 'frozen', False):
        # PyInstaller frozen: exe lives at <app_root>/MyToolbox.exe
        return Path(sys.executable).resolve().parent
    # Dev mode: core/__init__.py → core/ → <app_root>/
    return Path(__file__).resolve().parent.parent
