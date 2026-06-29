"""gui/theme.py — Centralized design tokens and theme system for MyToolbox.

Provides a cohesive dark visual identity inspired by modern developer tools.
All colour, font, spacing, and ttk style definitions live here so every GUI
module speaks the same design language.

Usage
-----
    from gui.theme import Theme, apply_theme

    theme = Theme.dark()          # or Theme.light()
    apply_theme(root, theme)      # configures ttk.Style + root bg
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass, field
from tkinter import ttk
from typing import ClassVar


# ═══════════════════════════════════════════════════════════════════════════
# Design tokens
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Theme:
    """Bundle of visual tokens for one theme variant."""

    name: str  # "dark" | "light"

    # ── Palette ────────────────────────────────────────────────────────
    bg_root: str
    bg_panel: str
    bg_card: str
    bg_input: str
    bg_hover: str
    bg_selected: str
    bg_canvas: str

    fg_primary: str
    fg_secondary: str
    fg_disabled: str
    fg_inverse: str  # text on accent backgrounds

    border: str
    border_focus: str
    accent: str
    accent_hover: str
    accent_pressed: str

    success: str
    warning: str
    danger: str
    danger_hover: str

    # ── Typography ──────────────────────────────────────────────────────
    font_family: str = "Microsoft YaHei UI"
    font_mono: str = "Consolas"

    # ── Spacing (8 px grid) ────────────────────────────────────────────
    space_xs: int = 4
    space_sm: int = 8
    space_md: int = 12
    space_lg: int = 16
    space_xl: int = 24

    # ── Sizes ───────────────────────────────────────────────────────────
    card_width: int = 200
    card_height: int = 220
    icon_size: int = 68
    sidebar_width: int = 180
    tree_row_height: int = 32

    # ═══════════════════════════════════════════════════════════════════
    # Presets
    # ═══════════════════════════════════════════════════════════════════

    _dark_defaults: ClassVar[dict] = {
        "bg_root":        "#1B1B2A",
        "bg_panel":       "#212136",
        "bg_card":        "#282840",
        "bg_input":       "#2E2E48",
        "bg_hover":       "#333358",
        "bg_selected":    "#1A3A5C",
        "bg_canvas":      "#191927",

        "fg_primary":     "#E4E4EC",
        "fg_secondary":   "#9D9DB9",
        "fg_disabled":    "#5A5A78",
        "fg_inverse":     "#FFFFFF",

        "border":         "#38385A",
        "border_focus":   "#4A9EFF",
        "accent":         "#4A9EFF",
        "accent_hover":   "#6DB5FF",
        "accent_pressed": "#3A8EE8",

        "success":        "#4ADE80",
        "warning":        "#F59E0B",
        "danger":         "#EF4444",
        "danger_hover":   "#F87171",
    }

    _light_defaults: ClassVar[dict] = {
        "bg_root":        "#F3F3F8",
        "bg_panel":       "#FFFFFF",
        "bg_card":        "#FFFFFF",
        "bg_input":       "#F9F9FC",
        "bg_hover":       "#EAEAF2",
        "bg_selected":    "#D6EBFF",
        "bg_canvas":      "#F0F0F5",

        "fg_primary":     "#1E1E2E",
        "fg_secondary":   "#6B6B80",
        "fg_disabled":    "#B0B0C0",
        "fg_inverse":     "#FFFFFF",

        "border":         "#E0E0EC",
        "border_focus":   "#4A9EFF",
        "accent":         "#3B82F6",
        "accent_hover":   "#2563EB",
        "accent_pressed": "#1D4ED8",

        "success":        "#22C55E",
        "warning":        "#F59E0B",
        "danger":         "#EF4444",
        "danger_hover":   "#DC2626",
    }

    @classmethod
    def dark(cls) -> "Theme":
        return cls(name="dark", **cls._dark_defaults)

    @classmethod
    def light(cls) -> "Theme":
        return cls(name="light", **cls._light_defaults)

    @classmethod
    def from_config(cls, config: dict) -> "Theme":
        """Resolve theme from config.json ``theme`` key."""
        name = config.get("theme", "dark")
        if name == "light":
            return cls.light()
        return cls.dark()


# ═══════════════════════════════════════════════════════════════════════════
# ttk Style application
# ═══════════════════════════════════════════════════════════════════════════

def apply_theme(root: tk.Tk, theme: Theme) -> ttk.Style:
    """Configure all ttk styles and root-level tk properties for *theme*."""

    style = ttk.Style(root)

    # ── Base theme ──────────────────────────────────────────────────────
    _pick_base_theme(style)

    # ── Root window ─────────────────────────────────────────────────────
    root.configure(bg=theme.bg_root)
    # Tk root uses ttk theme for ttk children; raw tk children need
    # explicit bg= passed.  We set the default tk background via option DB.
    root.option_add("*Background", theme.bg_root)
    root.option_add("*Foreground", theme.fg_primary)
    root.option_add("*Font",  (theme.font_family, 9))
    root.option_add("*selectBackground", theme.accent)
    root.option_add("*selectForeground", theme.fg_inverse)

    # ── TFrame ──────────────────────────────────────────────────────────
    style.configure("TFrame", background=theme.bg_root)
    style.configure("Panel.TFrame", background=theme.bg_panel)
    style.configure("Card.TFrame", background=theme.bg_card)
    style.configure("Toolbar.TFrame", background=theme.bg_panel)

    # ── TLabel ──────────────────────────────────────────────────────────
    style.configure("TLabel",
                    background=theme.bg_root,
                    foreground=theme.fg_primary,
                    font=(theme.font_family, 9))
    style.configure("Panel.TLabel",
                    background=theme.bg_panel,
                    foreground=theme.fg_primary)
    style.configure("Card.TLabel",
                    background=theme.bg_card,
                    foreground=theme.fg_primary)
    style.configure("Secondary.TLabel",
                    foreground=theme.fg_secondary,
                    font=(theme.font_family, 8))
    style.configure("Heading.TLabel",
                    foreground=theme.fg_primary,
                    font=(theme.font_family, 10, "bold"))
    style.configure("Toolbar.TLabel",
                    background=theme.bg_panel,
                    foreground=theme.fg_primary)

    # ── TButton ─────────────────────────────────────────────────────────
    style.configure("TButton",
                    background=theme.bg_input,
                    foreground=theme.fg_primary,
                    borderwidth=1,
                    relief=tk.FLAT,
                    padding=(theme.space_lg, theme.space_sm + 2),   # taller click target
                    font=(theme.font_family, 10))
    style.map("TButton",
              background=[("active", theme.bg_hover),
                          ("pressed", theme.bg_selected),
                          ("disabled", theme.bg_input)],
              foreground=[("active", theme.fg_primary),
                          ("disabled", theme.fg_disabled)],
              bordercolor=[("focus", theme.border_focus)])

    # Primary action button
    style.configure("Accent.TButton",
                    background=theme.accent,
                    foreground=theme.fg_inverse,
                    borderwidth=0,
                    relief=tk.FLAT,
                    padding=(theme.space_xl, theme.space_sm + 2),
                    font=(theme.font_family, 10, "bold"))
    style.map("Accent.TButton",
              background=[("active", theme.accent_hover),
                          ("pressed", theme.accent_pressed),
                          ("disabled", theme.bg_input)],
              foreground=[("active", theme.fg_inverse),
                          ("disabled", theme.fg_disabled)])

    # Danger button
    style.configure("Danger.TButton",
                    background=theme.danger,
                    foreground=theme.fg_inverse,
                    borderwidth=0,
                    relief=tk.FLAT,
                    padding=(theme.space_xl, theme.space_sm + 2),
                    font=(theme.font_family, 10))
    style.map("Danger.TButton",
              background=[("active", theme.danger_hover),
                          ("pressed", theme.danger),
                          ("disabled", theme.bg_input)],
              foreground=[("active", theme.fg_inverse),
                          ("disabled", theme.fg_disabled)])

    # Icon-only small button
    style.configure("Icon.TButton",
                    background=theme.bg_card,
                    foreground=theme.fg_secondary,
                    borderwidth=0,
                    relief=tk.FLAT,
                    padding=(theme.space_sm, theme.space_xs),
                    font=(theme.font_family, 9))
    style.map("Icon.TButton",
              background=[("active", theme.bg_hover)],
              foreground=[("active", theme.danger)])

    # ── TNotebook ───────────────────────────────────────────────────────
    style.configure("TNotebook",
                    background=theme.bg_root,
                    borderwidth=0,
                    tabmargins=(2, 2, 2, 0))
    style.configure("TNotebook.Tab",
                    background=theme.bg_panel,
                    foreground=theme.fg_secondary,
                    borderwidth=0,
                    padding=(theme.space_lg, theme.space_sm),
                    font=(theme.font_family, 10))
    style.map("TNotebook.Tab",
              background=[("selected", theme.bg_root),
                          ("active", theme.bg_hover)],
              foreground=[("selected", theme.fg_primary)],
              expand=[("selected", (0, 0, 0, 0))])

    # ── Treeview ────────────────────────────────────────────────────────
    style.configure("Treeview",
                    background=theme.bg_input,
                    foreground=theme.fg_primary,
                    fieldbackground=theme.bg_input,
                    borderwidth=0,
                    rowheight=theme.tree_row_height,
                    font=(theme.font_family, 9))
    style.configure("Treeview.Heading",
                    background=theme.bg_panel,
                    foreground=theme.fg_secondary,
                    borderwidth=0,
                    relief=tk.FLAT,
                    padding=(theme.space_sm, theme.space_sm),
                    font=(theme.font_family, 9, "bold"))
    style.map("Treeview",
              background=[("selected", theme.bg_selected)],
              foreground=[("selected", theme.fg_primary)])
    style.map("Treeview.Heading",
              background=[("active", theme.bg_hover)],
              foreground=[("active", theme.fg_primary)])

    # ── TCombobox ───────────────────────────────────────────────────────
    style.configure("TCombobox",
                    background=theme.bg_input,
                    foreground=theme.fg_primary,
                    fieldbackground=theme.bg_input,
                    borderwidth=1,
                    arrowcolor=theme.fg_secondary,
                    padding=(theme.space_sm, theme.space_xs),
                    font=(theme.font_family, 9))
    style.map("TCombobox",
              background=[("readonly", theme.bg_input),
                          ("active", theme.bg_hover)],
              fieldbackground=[("readonly", theme.bg_input)],
              foreground=[("readonly", theme.fg_primary)],
              bordercolor=[("focus", theme.border_focus)])

    # ── TEntry ──────────────────────────────────────────────────────────
    style.configure("TEntry",
                    background=theme.bg_input,
                    foreground=theme.fg_primary,
                    fieldbackground=theme.bg_input,
                    borderwidth=1,
                    padding=(theme.space_sm, theme.space_sm),
                    font=(theme.font_family, 9))
    style.map("TEntry",
              bordercolor=[("focus", theme.border_focus)])

    # ── TScrollbar ──────────────────────────────────────────────────────
    style.configure("TScrollbar",
                    background=theme.bg_panel,
                    troughcolor=theme.bg_root,
                    borderwidth=0,
                    arrowsize=0,
                    relief=tk.FLAT)
    style.map("TScrollbar",
              background=[("active", theme.bg_hover)])

    # ── TCheckbutton ────────────────────────────────────────────────────
    style.configure("TCheckbutton",
                    background=theme.bg_panel,
                    foreground=theme.fg_primary,
                    font=(theme.font_family, 9))
    style.map("TCheckbutton",
              background=[("active", theme.bg_panel)])

    # ── TRadiobutton ────────────────────────────────────────────────────
    style.configure("TRadiobutton",
                    background=theme.bg_panel,
                    foreground=theme.fg_primary,
                    font=(theme.font_family, 9))
    style.map("TRadiobutton",
              background=[("active", theme.bg_panel)])

    # ── TProgressbar (if used in future) ────────────────────────────────
    style.configure("TProgressbar",
                    background=theme.accent,
                    troughcolor=theme.bg_root,
                    borderwidth=0)

    # ── TSeparator ──────────────────────────────────────────────────────
    style.configure("TSeparator",
                    background=theme.border)

    # ── TLabelframe ─────────────────────────────────────────────────────
    style.configure("TLabelframe",
                    background=theme.bg_root,
                    foreground=theme.fg_primary,
                    borderwidth=1,
                    relief=tk.FLAT,
                    padding=(theme.space_md, theme.space_md))
    style.configure("TLabelframe.Label",
                    background=theme.bg_root,
                    foreground=theme.fg_primary,
                    font=(theme.font_family, 10, "bold"))

    return style


def _pick_base_theme(style: ttk.Style) -> None:
    """Force *clam* theme so custom colours are fully respected.

    On Windows, the default ``vista`` theme renders buttons with the OS
    theming engine, which ignores ttk ``background`` / ``foreground``
    overrides — text ends up near-invisible on dark backgrounds.  *clam*
    is drawn entirely in Tcl, so every style option we set takes effect.
    """
    try:
        style.theme_use("clam")
    except tk.TclError:
        # Absolute fallback — any theme is better than nothing.
        for candidate in ("alt", "default", "vista", "winnative"):
            try:
                style.theme_use(candidate)
                return
            except tk.TclError:
                continue


# ═══════════════════════════════════════════════════════════════════════════
# Helper: build a tk.Listbox styled to match the theme
# ═══════════════════════════════════════════════════════════════════════════

def themed_listbox(parent: tk.Widget, theme: Theme, **kw) -> tk.Listbox:
    """Return a tk.Listbox pre-styled for *theme*."""
    return tk.Listbox(
        parent,
        bg=theme.bg_panel,
        fg=theme.fg_primary,
        selectbackground=theme.accent,
        selectforeground=theme.fg_inverse,
        activestyle="none",
        highlightthickness=0,
        relief=tk.FLAT,
        borderwidth=0,
        font=(theme.font_family, 10),
        **kw,
    )


def themed_canvas(parent: tk.Widget, theme: Theme, **kw) -> tk.Canvas:
    """Return a tk.Canvas pre-styled for *theme*."""
    return tk.Canvas(
        parent,
        bg=theme.bg_canvas,
        highlightthickness=0,
        relief=tk.FLAT,
        **kw,
    )


def themed_text(parent: tk.Widget, theme: Theme, **kw) -> tk.Text:
    """Return a tk.Text pre-styled for *theme*."""
    return tk.Text(
        parent,
        bg=theme.bg_input,
        fg=theme.fg_primary,
        insertbackground=theme.fg_primary,
        selectbackground=theme.bg_selected,
        selectforeground=theme.fg_primary,
        relief=tk.FLAT,
        borderwidth=1,
        padx=theme.space_sm,
        pady=theme.space_sm,
        font=(theme.font_family, 9),
        **kw,
    )
