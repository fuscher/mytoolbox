"""gui/install_tab.py — Category sidebar + tool card grid with modern styling.

Each tool card shows:
  icon · name · version dropdown (if multi-version) · Install / Installed button
"""

from __future__ import annotations

import json
import os
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Callable, Dict, List, Optional, Set

from core.scanner import scan_tools
from core.state import load_state, is_installed
from core.installer import install_tool
from core.models import CategoryInfo, ScanResult, ToolInfo
from core.index_manager import IndexManager
from core.icon_extractor import IconExtractor
from core import get_app_root
from .dialogs import CategoryManageDialog, _BatchCategorizeDialog
from .theme import Theme, themed_listbox, themed_canvas
from .dialogs import themed_confirm, themed_warning, themed_info


CARD_W = 200        # fallback / default card width
CARD_H = 189        # card height (fixed, ~90% of original 210)
MIN_CARD_W = 160    # smallest card width for readability
MAX_CARD_W = 200    # largest card width to avoid excess whitespace
CARD_PAD_X = 10
CARD_PAD_Y = 10
ICON_SIZE = 64
ICON_PAD = 10
CIRCLE_SIZE = 74
GRID_COLUMNS = 4
INSPECTOR_WIDTH = 300

_INSTALLER_EXTENSIONS = {".exe", ".msi", ".msu", ".zip", ".7z", ".rar"}


def _bind_wheel_children(tab: "InstallTab", canvas: tk.Canvas,
                         handler: Callable) -> None:
    """Recursively bind ``<MouseWheel>`` on every child of *canvas*
    (and the windowed frame inside it) so scrolling works regardless
    of which card / label the pointer is over."""
    canvas.bind("<MouseWheel>", handler, add=True)
    # The real grid is inside a canvas window — get that frame
    children = canvas.winfo_children()
    for w in children:
        w.bind("<MouseWheel>", handler, add=True)
        # tk.Canvas.create_window embeds a tk.Frame; reach its kids
        if isinstance(w, tk.Frame):
            _bind_recursive(w, handler)


def _bind_recursive(widget: tk.Widget, handler: Callable) -> None:
    """Walk *widget*'s subtree and bind ``<MouseWheel>`` everywhere."""
    widget.bind("<MouseWheel>", handler, add=True)
    for child in widget.winfo_children():
        _bind_recursive(child, handler)


class InstallTab(ttk.Frame):
    """Tab 1: Browse and install tool packages."""

    def __init__(self, parent: tk.Widget, config: dict, theme: Theme,
                 set_status: Callable[[str], None]):
        super().__init__(parent)
        self.config = config
        self.t = theme
        self._set_status = set_status
        self._scan: Optional[ScanResult] = None
        self._selected_cat: Optional[str] = None
        self._card_frames: List[ttk.Frame] = []
        self._photo_refs: List[tk.PhotoImage] = []
        self._watcher = None
        self._watchdog_imported = False
        self._watchdog_path = None
        self._refresh_pending = False
        self._last_card_width = CARD_W
        self._icon_extractor = IconExtractor()
        self._selected_tools: Set[str] = set()
        self._batch_frame: Optional[tk.Frame] = None
        self._batch_mode_var: tk.BooleanVar = tk.BooleanVar(value=False)

        self._build_ui()
        self.refresh()
        self._start_file_watcher()

    def _build_ui(self) -> None:
        t = self.t

        # ── Sidebar ─────────────────────────────────────────────────────
        self._sidebar = tk.Frame(self, bg=t.bg_panel, width=t.sidebar_width)
        self._sidebar.pack(side=tk.LEFT, fill=tk.Y)
        self._sidebar.pack_propagate(False)

        # Sidebar header
        header = tk.Frame(self._sidebar, bg=t.bg_panel)
        header.pack(fill=tk.X, padx=t.space_md, pady=(t.space_md, t.space_sm))
        tk.Label(
            header, text="分类", bg=t.bg_panel, fg=t.fg_primary,
            font=(t.font_family, 11, "bold"),
        ).pack(side=tk.LEFT)

        # Category list
        self._cat_listbox = themed_listbox(self._sidebar, t, height=20)
        self._cat_listbox.pack(fill=tk.BOTH, expand=True, padx=t.space_sm, pady=(0, t.space_sm))
        self._cat_listbox.bind("<<ListboxSelect>>", self._on_cat_select)

        # Thin separator between sidebar and content
        sep = tk.Frame(self, bg=t.border, width=1)
        sep.pack(side=tk.LEFT, fill=tk.Y)

        # ── Main content area (card + inspector) ─────────────────────────
        main_area = tk.Frame(self, bg=t.bg_root)
        main_area.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Top bar (shared between card area and inspector)
        top_bar = tk.Frame(main_area, bg=t.bg_root)
        top_bar.pack(fill=tk.X, padx=t.space_md, pady=(t.space_md, t.space_sm))

        # Search entry
        tk.Label(
            top_bar, text="🔍", bg=t.bg_root, fg=t.fg_secondary,
            font=(t.font_family, 9),
        ).pack(side=tk.LEFT, padx=(0, t.space_xs))
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._rebuild_cards())
        search_entry = ttk.Entry(top_bar, textvariable=self._search_var, width=22)
        search_entry.pack(side=tk.LEFT, padx=(0, t.space_lg))

        # Action buttons (right-aligned)
        ttk.Button(
            top_bar, text="刷新列表", command=self.refresh, style="TButton"
        ).pack(side=tk.RIGHT, padx=(t.space_xs, 0))
        ttk.Button(
            top_bar, text="导入安装包", command=self._import_installer, style="Accent.TButton"
        ).pack(side=tk.RIGHT, padx=t.space_xs)
        ttk.Button(
            top_bar, text="分类管理", command=self._new_category, style="TButton"
        ).pack(side=tk.RIGHT, padx=t.space_xs)

        self._batch_mode_btn = ttk.Checkbutton(
            top_bar, text="批量选择", variable=self._batch_mode_var,
            command=self._on_batch_mode_toggle,
        )
        self._batch_mode_btn.pack(side=tk.RIGHT, padx=t.space_lg)

        # Separator
        tk.Frame(main_area, bg=t.border, height=1).pack(fill=tk.X, padx=t.space_md)

        # ── Card + Inspector area (grid layout) ──────────────────────────
        self._card_container = tk.Frame(main_area, bg=t.bg_root)
        self._card_container.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)
        self._card_container.grid_rowconfigure(0, weight=1)

        # Card canvas area (weight=1)
        card_frame = tk.Frame(self._card_container, bg=t.bg_canvas)
        card_frame.grid(row=0, column=0, sticky=tk.NSEW)
        self._card_container.grid_columnconfigure(0, weight=1)
        card_frame.grid_rowconfigure(0, weight=1)
        card_frame.grid_columnconfigure(0, weight=1)

        self._canvas = themed_canvas(card_frame, t)
        self._vscroll = ttk.Scrollbar(card_frame, orient=tk.VERTICAL, command=self._on_scroll)
        self._canvas.configure(yscrollcommand=self._vscroll.set)

        self._vscroll.grid(row=0, column=1, sticky=tk.NS)
        self._canvas.grid(row=0, column=0, sticky=tk.NSEW)

        # Bottom area for progress bar and batch frame
        bottom_frame = tk.Frame(card_frame, bg=t.bg_canvas)
        bottom_frame.grid(row=1, column=0, columnspan=2, sticky=tk.EW)
        card_frame.grid_rowconfigure(1, weight=0)

        # Bottom scroll progress bar
        self._progress_bar = tk.Canvas(bottom_frame, height=4, bg=t.scroll_progress_bg, highlightthickness=0)
        self._progress_bar.pack(side=tk.BOTTOM, fill=tk.X)
        self._progress_bar_rect = self._progress_bar.create_rectangle(
            0, 0, 0, 4, fill=t.scroll_progress_fill, outline=""
        )

        # Batch action bar (hidden by default)
        self._batch_frame = tk.Frame(bottom_frame, bg=t.bg_panel, height=60)
        self._batch_frame.pack(side=tk.BOTTOM, fill=tk.X)
        self._batch_frame.pack_forget()
        self._batch_frame.pack_propagate(False)

        # Separator between card area and inspector (1px)
        inspector_sep = tk.Frame(self._card_container, bg=t.border, width=1)
        inspector_sep.grid(row=0, column=1, sticky=tk.NS)

        # Inspector panel (weight=0, minsize=300)
        self._inspector_panel = tk.Frame(self._card_container, bg=t.bg_panel)
        self._inspector_panel.grid(row=0, column=2, sticky=tk.NSEW)
        self._card_container.grid_columnconfigure(2, weight=0, minsize=INSPECTOR_WIDTH)

        self._build_inspector()

        # Grid frame inside canvas
        self._grid_frame = tk.Frame(self._canvas, bg=t.bg_canvas)
        self._canvas_window = self._canvas.create_window(
            (0, 0), window=self._grid_frame, anchor=tk.NW
        )

        self._grid_frame.bind("<Configure>", self._on_grid_resize)
        self._canvas.bind("<Configure>", self._on_canvas_resize)
        self._canvas.bind("<MouseWheel>", self._on_mousewheel)
        # Propagate scroll events from all children to the canvas
        _bind_wheel_children(self, self._canvas, self._on_mousewheel)

        self._selected_tool_key = None

    def _build_inspector(self) -> None:
        t = self.t

        # Inspector header
        header = tk.Frame(self._inspector_panel, bg=t.bg_panel)
        header.pack(fill=tk.X, padx=t.space_md, pady=(t.space_md, t.space_sm))
        tk.Label(
            header, text="检查器", bg=t.bg_panel, fg=t.fg_primary,
            font=(t.font_family, 10, "bold"),
        ).pack(side=tk.LEFT)

        # Separator
        tk.Frame(self._inspector_panel, bg=t.border, height=1).pack(fill=tk.X)

        # Info scrollable area
        info_container = tk.Frame(self._inspector_panel, bg=t.bg_panel)
        info_container.pack(fill=tk.BOTH, expand=True, padx=t.space_md, pady=t.space_sm)

        info_canvas = themed_canvas(info_container, t)
        info_canvas.configure(bg=t.bg_panel)
        info_vscroll = ttk.Scrollbar(info_container, orient=tk.VERTICAL, command=info_canvas.yview)
        info_canvas.configure(yscrollcommand=info_vscroll.set)

        info_vscroll.pack(side=tk.RIGHT, fill=tk.Y)
        info_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._inspector_canvas = info_canvas
        info_canvas.bind("<MouseWheel>", self._on_inspector_wheel)

        self._inspector_info_frame = tk.Frame(info_canvas, bg=t.bg_panel)
        info_window = info_canvas.create_window((0, 0), window=self._inspector_info_frame, anchor=tk.NW)

        info_canvas.bind("<Configure>", lambda e, c=info_canvas, w=info_window: c.itemconfigure(w, width=e.width))
        self._inspector_info_frame.bind("<Configure>", lambda e, c=info_canvas: c.configure(scrollregion=c.bbox("all")))

        # Separator
        tk.Frame(self._inspector_panel, bg=t.border, height=1).pack(fill=tk.X)

        # Action buttons
        btn_frame = tk.Frame(self._inspector_panel, bg=t.bg_panel)
        btn_frame.pack(fill=tk.X, padx=t.space_md, pady=t.space_md)

        self._inspector_install_btn = ttk.Button(
            btn_frame, text="安装此工具", style="Accent.TButton",
            command=self._on_inspector_install,
        )
        self._inspector_install_btn.pack(fill=tk.X, pady=(0, t.space_sm))

        self._inspector_remove_btn = ttk.Button(
            btn_frame, text="移除工具", style="Danger.TButton",
            command=self._on_inspector_remove,
        )
        self._inspector_remove_btn.pack(fill=tk.X)

        # Empty state (must be called after buttons are created)
        self._show_empty_inspector()

    def _on_scroll(self, *args) -> None:
        self._canvas.yview(*args)
        self._clamp_canvas_view(self._canvas)
        self._update_progress_bar()

    def _on_mousewheel(self, event) -> None:
        self._canvas.yview_scroll(-event.delta // 60, "units")
        self._clamp_canvas_view(self._canvas)
        self._update_progress_bar()

    def _on_inspector_wheel(self, event) -> None:
        self._inspector_canvas.yview_scroll(-event.delta // 60, "units")
        self._clamp_canvas_view(self._inspector_canvas)

    @staticmethod
    def _clamp_canvas_view(canvas: tk.Canvas) -> None:
        """Prevent the scrolled canvas window from drifting past bounds
        and leaving a gap at top or bottom."""
        bbox = canvas.bbox("all")
        if not bbox:
            return
        content_h = bbox[3] - bbox[1]
        view_h = canvas.winfo_height()
        # Content fits entirely — no scrolling needed, pin to origin
        if content_h <= view_h:
            canvas.yview_moveto(0.0)
            return

        y0, y1 = canvas.yview()
        # Scrolled past top
        if y0 <= 0.0:
            canvas.yview_moveto(0.0)
        # Scrolled past bottom
        elif y1 >= 1.0:
            canvas.yview_moveto(1.0)

    def _show_empty_inspector(self) -> None:
        t = self.t
        for w in self._inspector_info_frame.winfo_children():
            w.destroy()
        empty_label = tk.Label(
            self._inspector_info_frame, text="点击工具卡片查看详情",
            bg=t.bg_panel, fg=t.fg_disabled, font=(t.font_family, 9),
        )
        empty_label.pack(pady=20)
        self._inspector_install_btn.state(["disabled"])
        self._inspector_remove_btn.state(["disabled"])

    def _update_inspector(self, key: str, tool: ToolInfo) -> None:
        t = self.t
        self._selected_tool_key = key

        for w in self._inspector_info_frame.winfo_children():
            w.destroy()

        name = tool.get("name") or tool.get("folder_name", "")
        version = tool.get("version") or ""

        tk.Label(
            self._inspector_info_frame, text="工具名称", bg=t.bg_panel,
            fg=t.fg_secondary, font=(t.font_family, 8),
        ).pack(anchor=tk.W)
        tk.Label(
            self._inspector_info_frame, text=f"{name} {version}", bg=t.bg_panel,
            fg=t.fg_primary, font=(t.font_family, 10, "bold"),
        ).pack(anchor=tk.W, pady=(0, t.space_sm))

        tk.Label(
            self._inspector_info_frame, text="文件说明", bg=t.bg_panel,
            fg=t.fg_secondary, font=(t.font_family, 8),
        ).pack(anchor=tk.W)
        desc = tool.get("description") or "无"
        tk.Label(
            self._inspector_info_frame, text=desc, bg=t.bg_panel,
            fg=t.fg_primary, font=(t.font_family, 9), wraplength=INSPECTOR_WIDTH - 40,
        ).pack(anchor=tk.W, pady=(0, t.space_sm))

        installers = tool.get("installers", [])
        if installers:
            installer_file = installers[0].get("file", "")
            file_name = installer_file.split("/")[-1].split("\\")[-1]
            ext = Path(file_name).suffix.lower()

            file_type_map = {
                ".exe": "可执行文件",
                ".msi": "Windows Installer",
                ".msu": "更新包",
                ".zip": "ZIP 压缩包",
                ".7z": "7-Zip 压缩包",
                ".rar": "RAR 压缩包",
            }

            tk.Label(
                self._inspector_info_frame, text="文件类型", bg=t.bg_panel,
                fg=t.fg_secondary, font=(t.font_family, 8),
            ).pack(anchor=tk.W)
            tk.Label(
                self._inspector_info_frame, text=file_type_map.get(ext, ext), bg=t.bg_panel,
                fg=t.fg_primary, font=(t.font_family, 9),
            ).pack(anchor=tk.W, pady=(0, t.space_sm))

            tools_dir = self._resolve_tools_dir()
            file_path = tools_dir / file_name
            if file_path.exists():
                pe_info = self._get_pe_version_info(str(file_path))

                tk.Label(
                    self._inspector_info_frame, text="文件版本", bg=t.bg_panel,
                    fg=t.fg_secondary, font=(t.font_family, 8),
                ).pack(anchor=tk.W)
                tk.Label(
                    self._inspector_info_frame, text=pe_info.get("FileVersion", "无"), bg=t.bg_panel,
                    fg=t.fg_primary, font=(t.font_family, 9),
                ).pack(anchor=tk.W, pady=(0, t.space_sm))

                tk.Label(
                    self._inspector_info_frame, text="产品名称", bg=t.bg_panel,
                    fg=t.fg_secondary, font=(t.font_family, 8),
                ).pack(anchor=tk.W)
                tk.Label(
                    self._inspector_info_frame, text=pe_info.get("ProductName", "无"), bg=t.bg_panel,
                    fg=t.fg_primary, font=(t.font_family, 9),
                ).pack(anchor=tk.W, pady=(0, t.space_sm))

                tk.Label(
                    self._inspector_info_frame, text="产品版本", bg=t.bg_panel,
                    fg=t.fg_secondary, font=(t.font_family, 8),
                ).pack(anchor=tk.W)
                tk.Label(
                    self._inspector_info_frame, text=pe_info.get("ProductVersion", "无"), bg=t.bg_panel,
                    fg=t.fg_primary, font=(t.font_family, 9),
                ).pack(anchor=tk.W, pady=(0, t.space_sm))

                file_size = file_path.stat().st_size
                tk.Label(
                    self._inspector_info_frame, text="文件大小", bg=t.bg_panel,
                    fg=t.fg_secondary, font=(t.font_family, 8),
                ).pack(anchor=tk.W)
                tk.Label(
                    self._inspector_info_frame, text=self._format_size(file_size), bg=t.bg_panel,
                    fg=t.fg_primary, font=(t.font_family, 9),
                ).pack(anchor=tk.W, pady=(0, t.space_sm))

                mtime = file_path.stat().st_mtime
                import time
                mod_date = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime))
                tk.Label(
                    self._inspector_info_frame, text="修改日期", bg=t.bg_panel,
                    fg=t.fg_secondary, font=(t.font_family, 8),
                ).pack(anchor=tk.W)
                tk.Label(
                    self._inspector_info_frame, text=mod_date, bg=t.bg_panel,
                    fg=t.fg_primary, font=(t.font_family, 9),
                ).pack(anchor=tk.W, pady=(0, t.space_sm))

        installed = is_installed(key, self.config)
        archive_extensions = {".zip", ".7z", ".rar"}
        is_archive = False
        if installers:
            installer_file = installers[0].get("file", "")
            ext = Path(installer_file).suffix.lower()
            is_archive = ext in archive_extensions

        if installed:
            self._inspector_install_btn.config(text="✔ 已安装")
            self._inspector_install_btn.state(["disabled"])
        elif is_archive:
            self._inspector_install_btn.config(text="打开压缩包")
            self._inspector_install_btn.state(["!disabled"])
        elif installers:
            self._inspector_install_btn.config(text="安装此工具")
            self._inspector_install_btn.state(["!disabled"])
        else:
            self._inspector_install_btn.config(text="无安装包")
            self._inspector_install_btn.state(["disabled"])

        self._inspector_remove_btn.state(["!disabled"])

    def _get_pe_version_info(self, file_path: str) -> dict:
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.windll.kernel32
            version = ctypes.windll.version

            file_path_w = ctypes.c_wchar_p(file_path)
            dummy = wintypes.DWORD()

            size = version.GetFileVersionInfoSizeW(file_path_w, ctypes.byref(dummy))
            if size == 0:
                return {}

            buffer = ctypes.create_string_buffer(size)
            if not version.GetFileVersionInfoW(file_path_w, 0, size, buffer):
                return {}

            def get_value(key):
                pvalue = wintypes.LPWSTR()
                value_size = wintypes.UINT()
                if version.VerQueryValueW(buffer, ctypes.c_wchar_p(key),
                                          ctypes.byref(pvalue), ctypes.byref(value_size)):
                    if pvalue:
                        return ctypes.wstring_at(pvalue)
                return ""

            info = {}
            info["FileVersion"] = get_value(r"\StringFileInfo\040904B0\FileVersion")
            info["ProductName"] = get_value(r"\StringFileInfo\040904B0\ProductName")
            info["ProductVersion"] = get_value(r"\StringFileInfo\040904B0\ProductVersion")
            info["FileDescription"] = get_value(r"\StringFileInfo\040904B0\FileDescription")
            info["CompanyName"] = get_value(r"\StringFileInfo\040904B0\CompanyName")

            return info
        except Exception:
            return {}

    def _format_size(self, size: int) -> str:
        if size < 1024:
            return f"{size} B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.2f} KB"
        elif size < 1024 * 1024 * 1024:
            return f"{size / (1024 * 1024):.2f} MB"
        else:
            return f"{size / (1024 * 1024 * 1024):.2f} GB"

    def _on_inspector_install(self) -> None:
        if not self._selected_tool_key or not self._scan:
            return
        tool = self._scan["tools"].get(self._selected_tool_key)
        if not tool:
            return
        version_var = tk.IntVar(value=0)
        installers = tool.get("installers", [])
        if len(installers) > 1 and hasattr(version_var, '_combo'):
            version_var.set(version_var._combo.current())
        self._on_install(self._selected_tool_key, tool, version_var)

    def _on_inspector_remove(self) -> None:
        if not self._selected_tool_key or not self._scan:
            return
        tool = self._scan["tools"].get(self._selected_tool_key)
        if not tool:
            return
        self._on_remove_tool(self._selected_tool_key, tool)

    def refresh(self) -> None:
        """Rescan tools/ and rebuild the UI."""
        self._detect_orphaned_files()
        self._rebuild_from_index()

    def _rebuild_from_index(self) -> None:
        """Rebuild UI from the current index (does not scan filesystem)."""
        self._scan = scan_tools(self.config)
        self._rebuild_cat_list()
        self._rebuild_cards()
        self._set_status("工具列表已刷新")

    def _detect_orphaned_files(self) -> None:
        """Find installer files in tools/ not yet tracked by the index.

        Handles files copied manually while the app was not running.
        Detected files are auto-imported or presented to the user.
        """
        tools_dir = self._resolve_tools_dir()
        if not tools_dir.is_dir():
            return

        manager = IndexManager(tools_dir)
        orphaned = []

        for root, dirs, files in os.walk(tools_dir):
            for f in files:
                ext = Path(f).suffix.lower()
                if ext not in _INSTALLER_EXTENSIONS:
                    continue
                full_path = Path(root) / f
                rel_path = full_path.relative_to(tools_dir)
                rel_str = str(rel_path).replace("\\", "/")
                found = False
                for tool in manager.get_all_tools():
                    for installer in tool.get("installers", []):
                        file_ref = installer.get("file", "")
                        if file_ref == rel_str or file_ref.endswith("/" + f):
                            found = True
                            break
                    if found:
                        break
                if not found:
                    orphaned.append((full_path, rel_str))

        if not orphaned:
            return

        self._import_files(orphaned)
        self._set_status(f"已自动导入 {len(orphaned)} 个新安装包")

    # ── File watcher ──────────────────────────────────────────────────────

    def _start_file_watcher(self) -> None:
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler

            class InstallerHandler(FileSystemEventHandler):
                def __init__(self, parent):
                    self.parent = parent

                def on_created(self, event):
                    if not event.is_directory:
                        self.parent._on_file_created(event.src_path)

                def on_modified(self, event):
                    if not event.is_directory:
                        self.parent._on_file_created(event.src_path)

            tools_dir = self._resolve_tools_dir()
            self._watchdog_path = tools_dir
            self._watchdog_imported = True

            self._event_handler = InstallerHandler(self)
            self._observer = Observer()
            self._observer.schedule(self._event_handler, str(tools_dir), recursive=True)
            self._observer.start()
            self._set_status("文件监听已启动")
        except ImportError:
            self._watchdog_imported = False
            self._set_status("文件监听未启用 (watchdog 未安装)")

    # ── Import / file handling ────────────────────────────────────────────

    def _resolve_tools_dir(self) -> Path:
        if self.config and "tools_dir" in self.config:
            return Path(self.config["tools_dir"]).resolve()
        return get_app_root() / "tools"

    def _on_file_created(self, file_path: str) -> None:
        if self._refresh_pending:
            return
        ext = Path(file_path).suffix.lower()
        if ext not in _INSTALLER_EXTENSIONS:
            return
        self._refresh_pending = True
        self.after(2000, self._process_pending_refresh)

    def _process_pending_refresh(self) -> None:
        self._refresh_pending = False
        file_path = Path(self._watchdog_path)
        manager = IndexManager(file_path)
        new_files = []
        for root, dirs, files in os.walk(self._watchdog_path):
            for f in files:
                ext = Path(f).suffix.lower()
                if ext in _INSTALLER_EXTENSIONS:
                    full_path = Path(root) / f
                    rel_path = full_path.relative_to(self._watchdog_path)
                    rel_str = str(rel_path).replace("\\", "/")
                    exists = False
                    for tool in manager.get_all_tools():
                        for installer in tool.get("installers", []):
                            if installer.get("file") == rel_str:
                                exists = True
                                break
                        if exists:
                            break
                    if not exists:
                        new_files.append((full_path, rel_str))
        if new_files:
            self.after(0, lambda: self._show_import_dialog(new_files))
        else:
            self._rebuild_from_index()

    def _show_import_dialog(self, new_files: List[tuple]) -> None:
        result = themed_confirm(
            self, "发现新安装包",
            f"检测到 {len(new_files)} 个新安装包，是否导入？\n\n"
            + "\n".join(f"  - {f[0].name}" for f in new_files[:5])
            + ("\n  ..." if len(new_files) > 5 else ""),
            self.t, icon="info",
        )
        if result:
            self._import_files(new_files)

    def _import_files(self, files: List[tuple]) -> None:
        tools_dir = self._resolve_tools_dir()
        manager = IndexManager(tools_dir)
        for full_path, rel_path in files:
            file_name = full_path.stem
            tool_name = file_name
            version = ""
            import re
            ver_match = re.search(r'[\d]+\.[\d]+(?:\.[\d]+)?', file_name)
            if ver_match:
                version = ver_match.group()
                tool_name = re.sub(r'[-_]?[\d]+\.[\d]+(?:\.[\d]+)?', '', file_name).strip('-_ ')
            ext = full_path.suffix.lower()
            installer_type = "exe_installer" if ext == ".exe" else "msi_installer" if ext == ".msi" else "archive"
            tool_info = {
                "name": tool_name,
                "version": version if version else None,
                "description": "",
                "categories": [],
                "type": installer_type,
                "installers": [{
                    "file": rel_path,
                    "label": f"v{version}" if version else "默认",
                }],
                "folder_path": rel_path.rsplit("/", 1)[0] if "/" in rel_path else "",
                "folder_name": file_name,
            }
            try:
                manager.add_tool(tool_info)
                self._set_status(f"已导入: {file_name}")
            except Exception as e:
                self._set_status(f"导入失败: {file_name}")
        self._rebuild_from_index()

    def _import_installer(self) -> None:
        try:
            import filedialog
        except ImportError:
            from tkinter import filedialog
        file_paths = filedialog.askopenfilenames(
            title="选择安装包文件",
            filetypes=[
                ("安装包文件", "*.exe *.msi *.msu"),
                ("压缩文件", "*.zip *.7z *.rar"),
                ("所有文件", "*.*"),
            ],
            parent=self,
        )
        if not file_paths:
            return
        tools_dir = self._resolve_tools_dir()
        manager = IndexManager(tools_dir)
        for file_path in file_paths:
            src_path = Path(file_path)
            ext = src_path.suffix.lower()
            if ext not in _INSTALLER_EXTENSIONS:
                themed_warning(self, "不支持的文件类型",
                               f"{src_path.name} 不是支持的安装包格式", self.t)
                continue
            dest_path = tools_dir / src_path.name
            counter = 1
            while dest_path.exists():
                dest_path = tools_dir / f"{src_path.stem}_{counter}{ext}"
                counter += 1
            try:
                import shutil
                shutil.copy2(src_path, dest_path)
                file_name = dest_path.stem
                tool_name = file_name
                version = ""
                import re
                ver_match = re.search(r'[\d]+\.[\d]+(?:\.[\d]+)?', file_name)
                if ver_match:
                    version = ver_match.group()
                    tool_name = re.sub(r'[-_]?[\d]+\.[\d]+(?:\.[\d]+)?', '', file_name).strip('-_ ')
                installer_type = "exe_installer" if ext == ".exe" else "msi_installer" if ext == ".msi" else "archive"
                tool_info = {
                    "name": tool_name,
                    "version": version if version else None,
                    "description": "",
                    "categories": [],
                    "type": installer_type,
                    "installers": [{
                        "file": dest_path.name,
                        "label": f"v{version}" if version else "默认",
                    }],
                    "folder_path": "",
                    "folder_name": file_name,
                }
                manager.add_tool(tool_info)
                self._set_status(f"已导入并添加: {file_name}")
            except Exception as e:
                self._set_status(f"导入失败: {file_name}")
        self._rebuild_from_index()

    def _new_category(self) -> None:
        tools_dir = self._resolve_tools_dir()
        dialog = CategoryManageDialog(self, tools_dir, self.t)
        self.wait_window(dialog)
        self.refresh()
        self._set_status("分类管理已关闭")

    # ── Batch operations ──────────────────────────────────────────────────

    def _on_card_select(self, key: str, var: tk.BooleanVar) -> None:
        if var.get():
            self._selected_tools.add(key)
        else:
            self._selected_tools.discard(key)
        self._update_batch_frame()

    def _update_batch_frame(self) -> None:
        t = self.t
        for w in self._batch_frame.winfo_children():
            w.destroy()
        if not self._batch_mode_var.get():
            self._batch_frame.pack_forget()
            return
        self._batch_frame.pack(side=tk.BOTTOM, fill=tk.X)

        # Top separator
        tk.Frame(self._batch_frame, bg=t.border, height=1).pack(fill=tk.X)

        row = tk.Frame(self._batch_frame, bg=t.bg_panel)
        row.pack(fill=tk.X, padx=t.space_md, pady=t.space_sm)

        if self._selected_tools:
            tk.Label(
                row, text=f"已选择 {len(self._selected_tools)} 个工具",
                bg=t.bg_panel, fg=t.accent, font=(t.font_family, 9, "bold"),
            ).pack(side=tk.LEFT)
        else:
            tk.Label(
                row, text="请选择工具", bg=t.bg_panel, fg=t.fg_secondary,
                font=(t.font_family, 9),
            ).pack(side=tk.LEFT)

        tools = self._scan["tools"] if self._scan else {}
        if self._selected_cat:
            tools = {k: v for k, v in tools.items() if self._selected_cat in v["categories"]}
        all_selected = len(self._selected_tools) == len(tools) and len(tools) > 0

        ttk.Button(row, text="删除选中", style="Danger.TButton",
                   command=self._batch_remove_tools).pack(side=tk.RIGHT, padx=(t.space_xs, 0))
        ttk.Button(row, text="分类到...", command=self._batch_categorize).pack(side=tk.RIGHT, padx=t.space_xs)
        ttk.Button(row, text="取消全选" if all_selected else "全选",
                   command=self._toggle_select_all).pack(side=tk.RIGHT, padx=t.space_xs)
        ttk.Button(row, text="取消选择",
                   command=self._clear_selection).pack(side=tk.RIGHT, padx=t.space_xs)

    def _clear_selection(self) -> None:
        self._selected_tools.clear()
        self._update_batch_frame()
        self._rebuild_cards()

    def _toggle_select_all(self) -> None:
        if not self._scan:
            return
        tools = self._scan["tools"]
        if self._selected_cat:
            tools = {k: v for k, v in tools.items() if self._selected_cat in v["categories"]}
        tool_keys = set(tools.keys())
        if self._selected_tools == tool_keys:
            self._selected_tools.clear()
        else:
            self._selected_tools = tool_keys
        self._update_batch_frame()
        self._rebuild_cards()

    def _on_batch_mode_toggle(self) -> None:
        if not self._batch_mode_var.get():
            self._selected_tools.clear()
        self._update_batch_frame()
        self._rebuild_cards()

    def _batch_remove_tools(self) -> None:
        if not self._scan or not self._selected_tools:
            return
        tools_dir = self._resolve_tools_dir()
        manager = IndexManager(tools_dir)
        selected_tool_names = []
        all_file_paths = []
        running_tools = []
        for key in self._selected_tools:
            tool = self._scan["tools"].get(key)
            if not tool:
                continue
            selected_tool_names.append(tool.get("name", ""))
            if is_installed(key, self.config):
                running_tools.append(tool.get("name", ""))
            for installer in tool.get("installers", []):
                file_name = installer.get("file", "")
                if "/" in file_name or "\\" in file_name:
                    file_name = file_name.split("/")[-1].split("\\")[-1]
                file_path = tools_dir / file_name
                if file_path.exists():
                    all_file_paths.append(file_path)
        if running_tools:
            result = themed_confirm(
                self, "确认删除",
                f"以下工具正在运行中，确定要删除吗？\n\n" + "\n".join(f"  - {name}" for name in running_tools),
                self.t, icon="danger",
            )
            if not result:
                return
        msg = f"确定要删除选中的 {len(self._selected_tools)} 个工具吗？"
        if all_file_paths:
            file_list = "\n".join(f"  - {p.name}" for p in all_file_paths[:10])
            if len(all_file_paths) > 10:
                file_list += f"\n  ... 等共 {len(all_file_paths)} 个文件"
            msg += "\n\n将删除以下文件：\n" + file_list
        result = themed_confirm(self, "确认删除", msg, self.t, icon="danger")
        if not result:
            return
        for key in list(self._selected_tools):
            tool = self._scan["tools"].get(key)
            if not tool:
                continue
            tool_id = tool.get("id", "")
            if not tool_id:
                continue
            for installer in tool.get("installers", []):
                file_name = installer.get("file", "")
                if "/" in file_name or "\\" in file_name:
                    file_name = file_name.split("/")[-1].split("\\")[-1]
                file_path = tools_dir / file_name
                if file_path.exists():
                    try:
                        file_path.unlink()
                    except OSError:
                        pass
            icon_name = tool.get("folder_name") or tool.get("id", "")
            icon_path = tools_dir / "_icons" / f"{icon_name}.png"
            if icon_path.exists():
                try:
                    icon_path.unlink()
                except OSError:
                    pass
            manager.delete_tool(tool_id)
        self._selected_tools.clear()
        self._selected_tool_key = None
        self.refresh()
        self._show_empty_inspector()
        self._set_status(f"已删除 {len(selected_tool_names)} 个工具")

    def _batch_categorize(self) -> None:
        if not self._scan:
            return
        cats = self._scan["categories"]
        cat_options = [(cat["code"], cat["display"]) for cat in cats]
        dialog = _BatchCategorizeDialog(self, cat_options, self.t)
        self.wait_window(dialog)
        if dialog.result:
            tools_dir = self._resolve_tools_dir()
            manager = IndexManager(tools_dir)
            tool_ids = []
            for key in self._selected_tools:
                tool = self._scan["tools"].get(key)
                if tool and tool.get("id"):
                    tool_ids.append(tool["id"])
            updated = manager.batch_update_categories(tool_ids, dialog.result)
            self._clear_selection()
            self.refresh()
            self._set_status(f"已将 {updated} 个工具分类到 {dialog.result}")

    def _on_remove_tool(self, key: str, tool: ToolInfo) -> None:
        tools_dir = self._resolve_tools_dir()
        manager = IndexManager(tools_dir)
        tool_id = tool.get("id", "")
        if not tool_id:
            return
        installed = is_installed(key, self.config)
        if installed:
            result = themed_confirm(
                self, "确认移除",
                f"该工具 {tool.get('name', '')} 正在运行中，确定要移除吗？",
                self.t, icon="danger",
            )
            if not result:
                return
        file_paths = []
        for installer in tool.get("installers", []):
            file_name = installer.get("file", "")
            if "/" in file_name or "\\" in file_name:
                file_name = file_name.split("/")[-1].split("\\")[-1]
            file_path = tools_dir / file_name
            if file_path.exists():
                file_paths.append(file_path)
        msg = f"确定要移除「{tool.get('name', '')}」吗？"
        if file_paths:
            msg += "\n\n将删除以下文件：\n" + "\n".join(f"  - {p.name}" for p in file_paths)
        result = themed_confirm(self, "确认移除", msg, self.t, icon="danger")
        if not result:
            return
        for file_path in file_paths:
            try:
                file_path.unlink()
            except OSError:
                pass
        icon_name = tool.get("folder_name") or tool.get("id", "")
        icon_path = tools_dir / "_icons" / f"{icon_name}.png"
        if icon_path.exists():
            try:
                icon_path.unlink()
            except OSError:
                pass
        manager.delete_tool(tool_id)
        self._selected_tools.discard(key)
        self._selected_tool_key = None
        self.refresh()
        self._show_empty_inspector()
        self._set_status(f"已移除: {tool.get('name', '')}")

    # ── Category list ─────────────────────────────────────────────────────

    def _rebuild_cat_list(self) -> None:
        self._cat_listbox.delete(0, tk.END)
        if not self._scan:
            return
        self._cat_listbox.insert(tk.END, "  全部")
        for cat in self._scan["categories"]:
            count = sum(1 for t in self._scan["tools"].values() if cat["code"] in t["categories"])
            self._cat_listbox.insert(tk.END, f"  {cat['display']}  ({count})")
        self._cat_listbox.selection_set(0)
        self._selected_cat = None

    def _on_cat_select(self, _event=None) -> None:
        sel = self._cat_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx == 0:
            self._selected_cat = None
        else:
            cats = self._scan["categories"]
            if idx - 1 < len(cats):
                self._selected_cat = cats[idx - 1]["code"]
        self._selected_tools.clear()
        self._update_batch_frame()
        self._rebuild_cards()

    # ── Card grid ─────────────────────────────────────────────────────────

    def _compute_card_width(self, canvas_w: int | None = None) -> int:
        """Return a responsive card width derived from the canvas width.

        The width is chosen as a percentage of the available area so that
        cards fill the grid evenly.  Clamped to [MIN_CARD_W, MAX_CARD_W].
        """
        if canvas_w is None:
            canvas_w = self._canvas.winfo_width()
        if canvas_w <= 0:
            return CARD_W  # default before first layout pass

        # Target column count: 4 on narrow windows up to ~8 on ultrawide
        # Pick the count that yields a card width closest to ~180 px
        best_w = MIN_CARD_W
        best_cols = 1
        for cols in range(4, 10):
            w = (canvas_w // cols) - CARD_PAD_X
            if MIN_CARD_W <= w <= MAX_CARD_W:
                return int(w)
            # track best fallback
            if abs(w - 180) < abs(best_w - 180):
                best_w = w
                best_cols = cols

        # fallback: clamp whatever we got
        return int(max(MIN_CARD_W, min(MAX_CARD_W, best_w)))

    def _rebuild_cards(self) -> None:
        for w in self._grid_frame.winfo_children():
            w.destroy()
        self._card_frames.clear()
        self._photo_refs.clear()

        if not self._scan:
            return

        tools = self._scan["tools"]
        if self._selected_cat:
            tools = {k: v for k, v in tools.items() if self._selected_cat in v["categories"]}

        # Apply search filter
        query = self._search_var.get().strip().lower()
        if query:
            tools = {
                k: v for k, v in tools.items()
                if query in v.get("name", "").lower()
                or query in v.get("description", "").lower()
            }

        t = self.t

        if not tools:
            empty_frame = tk.Frame(self._grid_frame, bg=t.bg_canvas)
            empty_frame.grid(row=0, column=0, padx=40, pady=60)
            tk.Label(
                empty_frame, text="📦", bg=t.bg_canvas, fg=t.fg_disabled,
                font=("Segoe UI Emoji", 36),
            ).pack()
            tk.Label(
                empty_frame, text="该分类下没有工具" if not query else "没有匹配的工具",
                bg=t.bg_canvas, fg=t.fg_secondary, font=(t.font_family, 11),
            ).pack(pady=(t.space_sm, 0))
            return

        card_width = self._compute_card_width()
        canvas_width = self._canvas.winfo_width()
        if canvas_width <= 0:
            canvas_width = 800
        columns = max(1, canvas_width // (card_width + CARD_PAD_X))

        state = load_state(self.config)

        col, row = 0, 0
        for key in sorted(tools.keys()):
            tool = tools[key]
            card = self._make_card(self._grid_frame, key, tool, state, card_width)
            card.grid(row=row, column=col, padx=CARD_PAD_X // 2, pady=CARD_PAD_Y // 2, sticky=tk.N)
            self._card_frames.append(card)
            col += 1
            if col >= columns:
                col = 0
                row += 1

        # Re-bind wheel to all new card children so scrolling works
        # wherever the mouse lands on the grid.
        _bind_wheel_children(self, self._canvas, self._on_mousewheel)

    def _make_card(self, parent: tk.Widget, key: str, tool: ToolInfo, state: dict,
                   card_width: int = CARD_W) -> tk.Frame:
        t = self.t

        # Card frame — tk.Frame for full bg colour control
        card = tk.Frame(parent, width=card_width, height=CARD_H, bg=t.bg_card,
                        highlightthickness=1, highlightbackground=t.border,
                        highlightcolor=t.border_focus)
        card.pack_propagate(False)

        # Batch-mode checkbox
        if self._batch_mode_var.get():
            var = tk.BooleanVar(value=key in self._selected_tools)
            cb = ttk.Checkbutton(
                card, variable=var,
                command=lambda k=key, v=var: self._on_card_select(k, v),
            )
            cb.place(x=t.space_xs, y=t.space_xs)

        # ── Icon ─────────────────────────────────────────────────────
        icon_path = self._tool_icon_path(tool)
        default_icon = get_app_root() / "resources" / "default_icon.png"
        is_default = icon_path == default_icon or not icon_path.exists()

        if is_default:
            icon_name = tool.get("folder_name") or tool.get("id", "")
            for installer in tool.get("installers", []):
                file_name = installer.get("file", "")
                if "/" in file_name or "\\" in file_name:
                    file_name = file_name.split("/")[-1].split("\\")[-1]
                file_path = self._resolve_tools_dir() / file_name
                if file_path.exists() and file_path.suffix.lower() in (".exe", ".msi"):
                    extracted = self._icon_extractor.extract_icon(str(file_path), icon_name)
                    if extracted:
                        icon_path = Path(extracted)
                        break

        installers = tool.get("installers", [])
        installed = is_installed(key, self.config)
        archive_extensions = {".zip", ".7z", ".rar"}
        is_archive = False
        if installers:
            installer_file = installers[0].get("file", "")
            ext = Path(installer_file).suffix.lower()
            is_archive = ext in archive_extensions

        can_install = not installed and (is_archive or installers)

        icon_container = tk.Canvas(
            card, width=CIRCLE_SIZE, height=CIRCLE_SIZE,
            bg=t.bg_card, highlightthickness=0,
            cursor="hand2" if can_install else "arrow",
        )
        icon_container.pack(pady=(t.space_md, t.space_xs))

        circle_fill = t.bg_input
        if can_install:
            circle_fill = t.bg_input

        circle_id = icon_container.create_oval(
            ICON_PAD // 2, ICON_PAD // 2, CIRCLE_SIZE - ICON_PAD // 2, CIRCLE_SIZE - ICON_PAD // 2,
            fill=circle_fill, outline="",
        )

        try:
            img = tk.PhotoImage(file=str(icon_path))
            iw, ih = img.width(), img.height()
            if iw > ICON_SIZE or ih > ICON_SIZE:
                factor_x = max(1, iw // ICON_SIZE)
                factor_y = max(1, ih // ICON_SIZE)
                img = img.subsample(factor_x, factor_y)
            self._photo_refs.append(img)
            icon_container.create_image(CIRCLE_SIZE // 2, CIRCLE_SIZE // 2, image=img)
        except tk.TclError:
            icon_container.create_text(
                CIRCLE_SIZE // 2, CIRCLE_SIZE // 2,
                text="📦", font=("Segoe UI Emoji", 24), fill=t.fg_disabled,
            )

        version_var = tk.IntVar(value=0)
        if len(installers) > 1:
            labels = [inst["label"] for inst in installers]
            combo = ttk.Combobox(card, values=labels, state="readonly", width=14)
            combo.current(0)
            combo.bind("<<ComboboxSelected>>",
                       lambda e, vv=version_var, cb=combo: vv.set(cb.current()))
            version_var._combo = combo

        def _on_icon_click(e):
            if can_install:
                if len(installers) > 1 and hasattr(version_var, '_combo'):
                    version_var.set(version_var._combo.current())
                self._on_install(key, tool, version_var)

        def _on_icon_enter(e):
            if can_install:
                icon_container.itemconfig(circle_id, fill=t.bg_hover)

        def _on_icon_leave(e):
            icon_container.itemconfig(circle_id, fill=t.bg_input)

        if can_install:
            icon_container.bind("<Button-1>", _on_icon_click)
            icon_container.bind("<Enter>", _on_icon_enter)
            icon_container.bind("<Leave>", _on_icon_leave)

        # ── Remove button (subtle) ──────────────────────────────────
        remove_btn = tk.Label(
            card, text="✕", bg=t.bg_card, fg=t.fg_disabled,
            font=(t.font_family, 10), cursor="hand2",
        )
        remove_btn.place(x=card_width - 26, y=t.space_xs)
        remove_btn.bind("<Button-1>", lambda e, k=key, tl=tool: self._on_remove_tool(k, tl))
        remove_btn.bind("<Enter>", lambda e, lbl=remove_btn: lbl.configure(fg=t.danger))
        remove_btn.bind("<Leave>", lambda e, lbl=remove_btn: lbl.configure(fg=t.fg_disabled))

        # ── Name ────────────────────────────────────────────────────
        name = tool.get("name") or tool["folder_name"]
        name_label = tk.Label(
            card, text=name, bg=t.bg_card, fg=t.fg_primary,
            font=(t.font_family, 9, "bold"), wraplength=card_width - 20, justify=tk.CENTER,
        )
        name_label.pack(pady=(0, 2))

        # ── Version ─────────────────────────────────────────────────
        if len(installers) > 1:
            ver_frame = tk.Frame(card, bg=t.bg_card)
            ver_frame.pack(pady=2)
            labels = [inst["label"] for inst in installers]
            combo = ttk.Combobox(ver_frame, values=labels, state="readonly", width=14)
            combo.current(0)
            combo.pack()
            combo.bind("<<ComboboxSelected>>",
                       lambda e, vv=version_var, cb=combo: vv.set(cb.current()))
            version_var._combo = combo
        else:
            ver = tool.get("version") or ""
            if ver:
                tk.Label(
                    card, text=f"v{ver}", bg=t.bg_card, fg=t.fg_secondary,
                    font=(t.font_family, 8),
                ).pack(pady=2)

        # ── Installed status indicator ──────────────────────────────
        if installed:
            tk.Label(
                card, text="✔ 已安装", bg=t.bg_card, fg=t.success,
                font=(t.font_family, 8),
            ).pack(pady=(6, t.space_md))
        elif is_archive:
            tk.Label(
                card, text="📁 压缩包", bg=t.bg_card, fg=t.fg_secondary,
                font=(t.font_family, 8),
            ).pack(pady=(6, t.space_md))
        elif installers:
            pass
        else:
            tk.Label(
                card, text="无安装包", bg=t.bg_card, fg=t.fg_disabled,
                font=(t.font_family, 8),
            ).pack(pady=(6, t.space_md))

        def _on_card_click(e):
            if e.widget != icon_container:
                self._update_inspector(key, tool)

        def _on_enter(e, c=card):
            c.configure(highlightbackground=t.border_focus)
        def _on_leave(e, c=card):
            c.configure(highlightbackground=t.border)

        card.bind("<Button-1>", _on_card_click)
        card.bind("<Enter>", _on_enter)
        card.bind("<Leave>", _on_leave)

        for child in card.winfo_children():
            if child != icon_container:
                child.bind("<Button-1>", _on_card_click)
                child.bind("<Enter>", _on_enter)
                child.bind("<Leave>", _on_leave)

        return card

    def _tool_icon_path(self, tool: ToolInfo) -> Path:
        tools_dir = Path(self.config.get("tools_dir", "")).resolve() if self.config.get("tools_dir") \
                    else get_app_root() / "tools"
        folder_path = tool.get("folder_path", "")
        if folder_path:
            icon = tools_dir / folder_path / "icon.png"
            if icon.exists():
                return icon
        icon_name = tool.get("folder_name") or tool.get("id", "")
        if icon_name:
            icon = tools_dir / icon_name / "icon.png"
            if icon.exists():
                return icon
            icon = tools_dir / f"{icon_name}.png"
            if icon.exists():
                return icon
        for installer in tool.get("installers", []):
            file_name = installer.get("file", "")
            if "/" in file_name or "\\" in file_name:
                file_name = file_name.split("/")[-1].split("\\")[-1]
            file_path = tools_dir / file_name
            if file_path.exists() and file_path.suffix.lower() in (".exe", ".msi"):
                icon_cache_dir = tools_dir / "_icons"
                icon_cache_dir.mkdir(exist_ok=True)
                cached_icon = icon_cache_dir / f"{icon_name}.png"
                if cached_icon.exists() and cached_icon.stat().st_size > 100:
                    return cached_icon
        default = get_app_root() / "resources" / "default_icon.png"
        return default if default.exists() else tools_dir / "icon.png"

    def _on_install(self, key: str, tool: ToolInfo, version_var: tk.IntVar) -> None:
        installer_idx = version_var.get()
        self._set_status(f"正在安装 {tool['name']}...")

        def on_status(status: str, detail: str):
            self.after(0, lambda: self._handle_install_status(key, status, detail))

        install_tool(tool, installer_idx, self.config, on_status)

    def _handle_install_status(self, key: str, status: str, detail: str) -> None:
        if status == "installed":
            self._set_status(f"✔ 已安装: {detail}")
            self.refresh()
        elif status == "timeout":
            self._set_status(f"⚠ {detail}")
            self.refresh()
        elif status == "error":
            self._set_status(f"❌ {detail}")
        elif status == "launching":
            self._set_status("正在启动安装程序...")
        elif status == "polling":
            self._set_status("等待安装完成...")

    def _on_grid_resize(self, _event=None) -> None:
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))
        self._update_progress_bar()

    def _on_canvas_resize(self, event) -> None:
        self._canvas.itemconfig(self._canvas_window, width=event.width)
        self._update_progress_bar()
        # Avoid full rebuild on every pixel-drag — only rebuild when
        # the responsive card width would actually change.
        new_width = self._compute_card_width(event.width)
        if abs(new_width - self._last_card_width) >= 1:
            self._last_card_width = new_width
            self._rebuild_cards()

    def _update_progress_bar(self) -> None:
        try:
            total = self._canvas.bbox("all")[3] if self._canvas.bbox("all") else 0
            visible = self._canvas.winfo_height()
            if total <= visible:
                self._progress_bar.coords(self._progress_bar_rect, 0, 0, 0, 4)
                return
            scroll_y = self._canvas.yview()[0]
            progress = scroll_y / (total - visible) * visible if total > visible else 0
            bar_width = self._progress_bar.winfo_width()
            fill_width = int(bar_width * (visible / total))
            x_pos = int(bar_width * progress)
            self._progress_bar.coords(self._progress_bar_rect, x_pos, 0, x_pos + fill_width, 4)
        except Exception:
            pass

    def destroy(self) -> None:
        if self._watcher and self._watchdog_imported:
            try:
                self._observer.stop()
                self._observer.join()
            except Exception:
                pass
        super().destroy()
