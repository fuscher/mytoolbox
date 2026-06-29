"""gui/app.py — Main application window.

Creates the top-level tkinter window with a toolbar and a two-tab
Notebook (安装包  /  启动器).
"""

from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Optional

from .install_tab import InstallTab
from .uninstaller_tab import UninstallerTab


class App(tk.Tk):
    """MyToolbox main window."""

    def __init__(self, config: Optional[dict] = None):
        super().__init__()

        # ── Config ──────────────────────────────────────────────────────
        self.config = config or self._load_config()

        # ── Window chrome ───────────────────────────────────────────────
        self.title("私人工具箱")
        self.geometry("1100x700")
        self.minsize(800, 500)
        self._centre_window(1100, 700)

        # ── Style ───────────────────────────────────────────────────────
        self._apply_style()

        # ── Toolbar ─────────────────────────────────────────────────────
        toolbar = ttk.Frame(self, padding=(8, 4))
        toolbar.pack(fill=tk.X)

        self.status_label = ttk.Label(toolbar, text="就绪")
        self.status_label.pack(side=tk.LEFT, padx=4)

        # ── Notebook (tabs) ─────────────────────────────────────────────
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))

        self.install_tab = InstallTab(self.notebook, self.config, self._set_status)
        self.uninstaller_tab = UninstallerTab(self.notebook, self.config, self._set_status)

        self.notebook.add(self.install_tab, text="  📦 安装包  ")
        self.notebook.add(self.uninstaller_tab, text="  🗑 应用管理  ")

    # ── Helpers ──────────────────────────────────────────────────────────

    def _set_status(self, text: str) -> None:
        self.status_label.config(text=text)

    def _apply_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("vista")          # Windows-native look
        except tk.TclError:
            try:
                style.theme_use("clam")       # Fallback on non-Windows
            except tk.TclError:
                pass

        # Notebook tab font
        style.configure("TNotebook.Tab", font=("Microsoft YaHei UI", 10))
        style.configure("TButton",     font=("Microsoft YaHei UI", 9))
        style.configure("TLabel",      font=("Microsoft YaHei UI", 9))

    def _centre_window(self, w: int, h: int) -> None:
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

    @staticmethod
    def _load_config() -> dict:
        cfg_path = Path(__file__).resolve().parent.parent / "config.json"
        if cfg_path.exists():
            try:
                return json.loads(cfg_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {}
