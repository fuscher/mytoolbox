"""gui/app.py — Main application window.

Creates the top-level tkinter window with a themed toolbar and a two-tab
Notebook (安装包  /  应用管理).
"""

from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Optional

from .theme import Theme, apply_theme, themed_canvas
from .install_tab import InstallTab
from .uninstaller_tab import UninstallerTab

# Paths the app looks up at startup
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_RESOURCES = _PROJECT_ROOT / "resources"


def _resolve_icon() -> str | None:
    """Return the best available window-icon path, if any."""
    # prefer .ico on Windows (taskbar shows it properly)
    ico = _RESOURCES / "app_icon.ico"
    if ico.exists():
        return str(ico)
    png = _RESOURCES / "app_icon.png"
    if png.exists():
        return str(png)
    return None


class App(tk.Tk):
    """MyToolbox main window."""

    def __init__(self, config: Optional[dict] = None):
        super().__init__()

        # ── Config ──────────────────────────────────────────────────────
        self.config = config or self._load_config()
        self._theme = Theme.from_config(self.config)

        # ── Window chrome ───────────────────────────────────────────────
        self.title("MyToolbox")
        self.geometry("1200x780")
        self.minsize(900, 600)
        self._centre_window(1200, 780)

        # ── Icon (title-bar / taskbar) ──────────────────────────────────
        icon_path = _resolve_icon()
        if icon_path:
            try:
                # .ico → iconbitmap; .png → iconphoto
                if icon_path.lower().endswith(".ico"):
                    self.iconbitmap(default=icon_path)
                else:
                    img = tk.PhotoImage(file=icon_path)
                    self.iconphoto(True, img)
                    self._icon_ref = img  # keep alive
            except tk.TclError:
                pass  # icon is cosmetic — never block launch

        # ── Theme (must happen before any widget creation) ──────────────
        self.style = apply_theme(self, self._theme)

        # ── Toolbar ─────────────────────────────────────────────────────
        self._build_toolbar()

        # ── Notebook (tabs) ─────────────────────────────────────────────
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=0, pady=(0, 0))

        self.install_tab = InstallTab(self.notebook, self.config, self._theme, self._set_status)
        self.uninstaller_tab = UninstallerTab(self.notebook, self.config, self._theme, self._set_status)

        self.notebook.add(self.install_tab, text="  安装包  ")
        self.notebook.add(self.uninstaller_tab, text="  应用管理  ")

    # ── Toolbar ───────────────────────────────────────────────────────────

    def _build_toolbar(self) -> None:
        t = self._theme
        toolbar = tk.Frame(self, bg=t.bg_panel, height=40)
        toolbar.pack(fill=tk.X)
        toolbar.pack_propagate(False)

        # App title
        title_lbl = tk.Label(
            toolbar,
            text="MyToolbox",
            bg=t.bg_panel,
            fg=t.fg_primary,
            font=(t.font_family, 11, "bold"),
        )
        title_lbl.pack(side=tk.LEFT, padx=(t.space_lg, t.space_xl))

        # Status indicator dot
        self._status_canvas = themed_canvas(toolbar, t, width=20, height=20)
        self._status_canvas.pack(side=tk.LEFT, padx=(0, t.space_sm))
        self._status_canvas.configure(bg=t.bg_panel, highlightthickness=0)
        self._status_dot = self._status_canvas.create_oval(
            4, 4, 16, 16, fill=t.success, outline="", tags="dot"
        )

        # Status text
        self.status_label = tk.Label(
            toolbar,
            text="就绪",
            bg=t.bg_panel,
            fg=t.fg_secondary,
            font=(t.font_family, 9),
        )
        self.status_label.pack(side=tk.LEFT)

        # Spacer
        tk.Frame(toolbar, bg=t.bg_panel).pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Separator line at bottom
        sep = tk.Frame(self, bg=t.border, height=1)
        sep.pack(fill=tk.X)

    # ── Status helpers ────────────────────────────────────────────────────

    def _set_status(self, text: str) -> None:
        """Update the toolbar status text and indicator colour."""
        self.status_label.config(text=text)

        t = self._theme
        # Pick dot colour from status text
        if text.startswith("✔") or text.startswith("已"):
            colour = t.success
        elif text.startswith("❌") or text.startswith("⚠") or "失败" in text:
            colour = t.danger if "失败" in text else t.warning
        elif "..." in text or "正在" in text or "扫描" in text:
            colour = t.warning
        elif text == "就绪":
            colour = t.success
        else:
            colour = t.accent

        self._status_canvas.itemconfig(self._status_dot, fill=colour)

    # ── Helpers ──────────────────────────────────────────────────────────

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
