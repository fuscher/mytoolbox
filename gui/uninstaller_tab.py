"""gui/uninstaller_tab.py — Installed applications management Tab.

Displays installed programs with search, uninstall, open-location,
and residual-junk cleanup support.
"""

from __future__ import annotations

import ctypes
import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk, messagebox
from typing import Callable, Dict, List, Optional

from core.app_manager import InstalledApp, scan_installed_apps, scan_installed_apps_async, uninstall_app, open_install_location, get_app_icon_path, UninstallerType
from core.junk_scanner import JunkManager, JunkResult, ConfidenceLevel
from .theme import Theme, themed_canvas


class JunkCleanupDialog(tk.Toplevel):
    """Residual-junk cleanup dialog."""

    def __init__(self, parent: tk.Widget, app_name: str, junk: List[JunkResult], theme: Theme):
        super().__init__(parent)
        self.t = theme
        self.configure(bg=theme.bg_root)
        self.title(f"清理 {app_name} 的残留")
        self.geometry("720x520")
        self.resizable(True, True)
        self.minsize(500, 400)
        self._junk = junk
        self._selected: List[str] = []

        self._build_ui()
        self.transient(parent)
        self.grab_set()

    def _build_ui(self) -> None:
        t = self.t

        # Stats bar
        stats_frame = tk.Frame(self, bg=t.bg_panel, padx=t.space_md, pady=t.space_sm)
        stats_frame.pack(fill=tk.X)

        total_size = sum(j.size or 0 for j in self._junk)
        if total_size >= 1024 * 1024:
            size_str = f"{total_size / (1024 * 1024):.1f} GB"
        elif total_size >= 1024:
            size_str = f"{total_size / 1024:.1f} MB"
        else:
            size_str = f"{total_size} KB"

        tk.Label(
            stats_frame, text=f"共发现 {len(self._junk)} 项残留，总计 {size_str}",
            bg=t.bg_panel, fg=t.fg_primary, font=(t.font_family, 9),
        ).pack(side=tk.LEFT)

        # Confidence filter
        filter_frame = tk.Frame(stats_frame, bg=t.bg_panel)
        filter_frame.pack(side=tk.RIGHT)

        self._confidence_var = tk.StringVar(value="all")
        level_labels = {"all": "全部", "certain": "确定", "high": "高", "medium": "中", "low": "低"}
        for level, label in level_labels.items():
            ttk.Radiobutton(
                filter_frame, text=label, variable=self._confidence_var,
                value=level, command=self._apply_filter,
            ).pack(side=tk.LEFT, padx=2)

        # Separator
        tk.Frame(self, bg=t.border, height=1).pack(fill=tk.X)

        # Treeview
        list_frame = tk.Frame(self, bg=t.bg_root)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=t.space_md, pady=t.space_md)

        columns = ("type", "path", "confidence", "description", "size")
        self._tree = ttk.Treeview(list_frame, columns=columns, show="headings", selectmode="extended")

        self._tree.heading("type", text="类型")
        self._tree.heading("path", text="路径")
        self._tree.heading("confidence", text="置信度")
        self._tree.heading("description", text="描述")
        self._tree.heading("size", text="大小")

        self._tree.column("type", width=80, minwidth=60)
        self._tree.column("path", width=280, minwidth=180)
        self._tree.column("confidence", width=70, minwidth=60)
        self._tree.column("description", width=140, minwidth=100)
        self._tree.column("size", width=70, minwidth=60)

        vscroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self._tree.yview)
        self._tree.configure(yscrollcommand=vscroll.set)

        vscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._tree.bind("<ButtonRelease-1>", self._on_select)

        # Bottom buttons
        btn_frame = tk.Frame(self, bg=t.bg_panel, padx=t.space_md, pady=t.space_sm)
        btn_frame.pack(fill=tk.X)

        tk.Frame(self, bg=t.border, height=1).pack(fill=tk.X)

        ttk.Button(btn_frame, text="全选", command=self._select_all).pack(side=tk.LEFT, padx=(0, t.space_xs))
        ttk.Button(btn_frame, text="反选", command=self._invert_select).pack(side=tk.LEFT, padx=(0, t.space_xs))

        ttk.Button(btn_frame, text="清理选中", style="Danger.TButton",
                   command=self._cleanup_selected).pack(side=tk.RIGHT, padx=(t.space_xs, 0))
        ttk.Button(btn_frame, text="取消", command=self.destroy).pack(side=tk.RIGHT, padx=(0, t.space_xs))

        self._populate_list()

    def _populate_list(self) -> None:
        self._tree.delete(*self._tree.get_children())
        for item in self._junk:
            confidence_text = {
                ConfidenceLevel.LOW: "低", ConfidenceLevel.MEDIUM: "中",
                ConfidenceLevel.HIGH: "高", ConfidenceLevel.CERTAIN: "确定",
            }[item.confidence]
            values = (
                item.type.value, item.path, confidence_text,
                item.description, item.get_size_display(),
            )
            self._tree.insert("", "end", values=values, tags=(item.path,))

    def _apply_filter(self) -> None:
        filter_level = self._confidence_var.get()
        self._tree.delete(*self._tree.get_children())
        level_map = {
            "certain": ConfidenceLevel.CERTAIN, "high": ConfidenceLevel.HIGH,
            "medium": ConfidenceLevel.MEDIUM, "low": ConfidenceLevel.LOW,
        }
        for item in self._junk:
            if filter_level == "all" or item.confidence == level_map.get(filter_level):
                confidence_text = {
                    ConfidenceLevel.LOW: "低", ConfidenceLevel.MEDIUM: "中",
                    ConfidenceLevel.HIGH: "高", ConfidenceLevel.CERTAIN: "确定",
                }[item.confidence]
                values = (
                    item.type.value, item.path, confidence_text,
                    item.description, item.get_size_display(),
                )
                self._tree.insert("", "end", values=values, tags=(item.path,))

    def _on_select(self, event) -> None:
        self._selected = [self._tree.item(i, "tags")[0] for i in self._tree.selection()]

    def _select_all(self) -> None:
        self._tree.selection_set(self._tree.get_children())
        self._on_select(None)

    def _invert_select(self) -> None:
        selected = set(self._tree.selection())
        all_items = set(self._tree.get_children())
        self._tree.selection_set(list(all_items - selected))
        self._on_select(None)

    def _cleanup_selected(self) -> None:
        if not self._selected:
            messagebox.showwarning("提示", "请先选择要清理的项")
            return
        selected_junk = [j for j in self._junk if j.path in self._selected]
        if not selected_junk:
            return
        if not messagebox.askyesno("确认清理",
                                   f"确定要清理以下 {len(selected_junk)} 项残留吗？\n\n此操作不可撤销！", parent=self):
            return
        manager = JunkManager()
        deleted, skipped = manager.delete_junk(selected_junk)
        messagebox.showinfo("清理完成",
                            f"已清理 {deleted} 项残留\n跳过 {skipped} 项（低置信度）", parent=self)
        self.destroy()


class UninstallerTab(ttk.Frame):
    """Tab: Installed applications management."""

    def __init__(self, parent: tk.Widget, config: dict, theme: Theme,
                 set_status: Callable[[str], None]):
        super().__init__(parent)
        self.config = config
        self.t = theme
        self._set_status = set_status
        self._apps: List[InstalledApp] = []
        self._filtered: List[InstalledApp] = []
        self._photo_refs: Dict[str, tk.PhotoImage] = {}
        self._icon_cache: Dict[str, tk.PhotoImage] = {}
        self._auto_scan_junk = tk.BooleanVar(value=True)
        self._scanning_app: Optional[str] = None

        self._build_ui()
        self._start_scan()

    def _build_ui(self) -> None:
        t = self.t

        # Top bar
        top = tk.Frame(self, bg=t.bg_panel, padx=t.space_md, pady=t.space_sm)
        top.pack(fill=tk.X)

        # Search
        tk.Label(
            top, text="🔍", bg=t.bg_panel, fg=t.fg_secondary,
            font=(t.font_family, 10),
        ).pack(side=tk.LEFT)
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._apply_filter())
        search_entry = ttk.Entry(top, textvariable=self._search_var, width=28)
        search_entry.pack(side=tk.LEFT, padx=(t.space_sm, t.space_lg))

        # Info label
        self._info_label = tk.Label(
            top, text="正在扫描...", bg=t.bg_panel, fg=t.fg_secondary,
            font=(t.font_family, 9),
        )
        self._info_label.pack(side=tk.LEFT)

        # Right-side controls
        tk.Frame(top, bg=t.bg_panel).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Checkbutton(top, text="卸载后自动扫描残留",
                        variable=self._auto_scan_junk).pack(side=tk.RIGHT, padx=(0, t.space_sm))
        ttk.Button(top, text="刷新", command=self._refresh).pack(side=tk.RIGHT, padx=(0, t.space_xs))

        # Separator
        tk.Frame(self, bg=t.border, height=1).pack(fill=tk.X)

        # Treeview frame
        list_frame = tk.Frame(self, bg=t.bg_root)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)

        columns = ("name", "publisher", "version", "size", "install_date", "type")
        self._tree = ttk.Treeview(list_frame, columns=columns, show="headings", selectmode="browse")

        self._tree.heading("name", text="名称")
        self._tree.heading("publisher", text="发布者")
        self._tree.heading("version", text="版本")
        self._tree.heading("size", text="大小")
        self._tree.heading("install_date", text="安装日期")
        self._tree.heading("type", text="类型")

        self._tree.column("name", width=220, minwidth=160)
        self._tree.column("publisher", width=130, minwidth=100)
        self._tree.column("version", width=80, minwidth=60)
        self._tree.column("size", width=70, minwidth=60)
        self._tree.column("install_date", width=110, minwidth=80)
        self._tree.column("type", width=60, minwidth=50)

        vscroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self._tree.yview)
        self._tree.configure(yscrollcommand=vscroll.set)

        vscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Zebra striping via tags
        self._tree.tag_configure("even", background=t.bg_input)
        self._tree.tag_configure("odd", background=t.bg_root)

        # Separator
        tk.Frame(self, bg=t.border, height=1).pack(fill=tk.X)

        # Bottom action bar
        btn_frame = tk.Frame(self, bg=t.bg_panel, padx=t.space_md, pady=t.space_sm)
        btn_frame.pack(fill=tk.X)

        ttk.Button(btn_frame, text="卸载选中", style="Danger.TButton",
                   command=self._uninstall_selected).pack(side=tk.LEFT, padx=(0, t.space_sm))
        ttk.Button(btn_frame, text="打开目录", command=self._open_location).pack(side=tk.LEFT)

        # Context menu
        self._context_menu = tk.Menu(self, tearoff=0, bg=t.bg_panel, fg=t.fg_primary,
                                     activebackground=t.bg_selected, activeforeground=t.fg_primary,
                                     font=(t.font_family, 9))
        self._context_menu.add_command(label="卸载", command=self._uninstall_selected)
        self._context_menu.add_command(label="打开安装目录", command=self._open_location)
        self._context_menu.add_separator()
        self._context_menu.add_command(label="复制名称", command=self._copy_name)
        self._context_menu.add_command(label="复制发布者", command=self._copy_publisher)

        self._tree.bind("<Button-3>", self._show_context_menu)
        self._tree.bind("<Double-1>", lambda e: self._uninstall_selected())

    def _start_scan(self) -> None:
        self._set_status("正在扫描已安装程序...")
        self._tree.delete(*self._tree.get_children())
        self._tree.insert("", "end", values=("正在扫描...", "", "", "", "", ""))
        scan_installed_apps_async(self._on_scan_done)

    def _on_scan_done(self, apps: List[InstalledApp]) -> None:
        self._apps = apps
        self._filtered = apps
        self._rebuild_list()
        self._info_label.config(text=f"共 {len(apps)} 个程序")
        self._set_status(f"扫描完成，共 {len(apps)} 个程序")

    def _refresh(self) -> None:
        self._start_scan()

    def _apply_filter(self) -> None:
        query = self._search_var.get().strip().lower()
        if query:
            self._filtered = [
                a for a in self._apps
                if query in a.name.lower()
                or (a.publisher and query in a.publisher.lower())
            ]
        else:
            self._filtered = self._apps
        self._rebuild_list()
        self._info_label.config(text=f"显示 {len(self._filtered)} / {len(self._apps)} 个")

    def _rebuild_list(self) -> None:
        self._tree.delete(*self._tree.get_children())

        type_map = {
            UninstallerType.MSI: "MSI", UninstallerType.NSIS: "NSIS",
            UninstallerType.INNO_SETUP: "Inno", UninstallerType.STORE_APP: "UWP",
            UninstallerType.EXE: "EXE", UninstallerType.CHOCOLATEY: "Choco",
            UninstallerType.UNKNOWN: "-",
        }

        for i, app in enumerate(self._filtered):
            values = (
                app.name,
                app.publisher or "-",
                app.version or "-",
                app.get_size_display(),
                app.install_date or "-",
                type_map.get(app.uninstaller_kind, "-"),
            )
            tag = "even" if i % 2 == 0 else "odd"
            self._tree.insert("", "end", values=values, tags=(tag,))

    def _get_selected_app(self) -> Optional[InstalledApp]:
        selection = self._tree.selection()
        if not selection:
            return None
        item_id = selection[0]
        idx = self._tree.get_children().index(item_id)
        if idx < len(self._filtered):
            return self._filtered[idx]
        return None

    def _uninstall_selected(self) -> None:
        app = self._get_selected_app()
        if not app:
            self._set_status("请先选择一个程序")
            return
        if not app.can_uninstall():
            self._set_status(f"{app.name} 无法卸载（系统组件）")
            return

        detail = f"发布者: {app.publisher or '未知'}\n"
        detail += f"版本: {app.version or '未知'}\n"
        detail += f"类型: {app.uninstaller_kind.value}\n"
        if app.install_location:
            detail += f"安装路径: {app.install_location}"

        if not messagebox.askyesno("确认卸载",
                                   f"确定要卸载 \"{app.name}\" 吗？\n\n{detail}",
                                   parent=self):
            return

        self._set_status(f"正在启动 {app.name} 的卸载程序...")
        success, msg = uninstall_app(app, use_quiet=False)

        if success:
            self._set_status(msg)
            if self._auto_scan_junk.get():
                self.after(5000, lambda: self._auto_scan_after_uninstall(app))
        else:
            self._set_status(f"卸载失败: {msg}")

    def _auto_scan_after_uninstall(self, app: InstalledApp) -> None:
        self._scanning_app = app.name
        self._set_status(f"正在后台扫描 {app.name} 的残留...")

        def scan_worker():
            manager = JunkManager()

            def on_progress(current: int, total: int, msg: str):
                self.after(0, lambda: self._set_status(f"扫描中 ({current}/{total}): {msg}"))

            junk = manager.scan_junk(app.name, app.install_location, app.registry_path, on_progress)
            self.after(0, lambda: self._on_scan_junk_done(app.name, junk))

        thread = threading.Thread(target=scan_worker, daemon=True)
        thread.start()

    def _on_scan_junk_done(self, app_name: str, junk: List[JunkResult]) -> None:
        self._scanning_app = None
        if not junk:
            self._set_status(f"未发现 {app_name} 的残留")
            return
        self._set_status(f"发现 {len(junk)} 项残留")
        JunkCleanupDialog(self, app_name, junk, self.t)

    def _open_location(self) -> None:
        app = self._get_selected_app()
        if not app:
            self._set_status("请先选择一个程序")
            return
        if open_install_location(app):
            self._set_status(f"已打开 {app.name} 的安装目录")
        else:
            self._set_status(f"无法打开 {app.name} 的安装目录")

    def _copy_name(self) -> None:
        app = self._get_selected_app()
        if app:
            self.clipboard_clear()
            self.clipboard_append(app.name)
            self._set_status(f"已复制: {app.name}")

    def _copy_publisher(self) -> None:
        app = self._get_selected_app()
        if app:
            self.clipboard_clear()
            self.clipboard_append(app.publisher or "")
            self._set_status(f"已复制: {app.publisher or '-'}")

    def _show_context_menu(self, event) -> None:
        item = self._tree.identify_row(event.y)
        if item:
            self._tree.selection_set(item)
        try:
            self._context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._context_menu.grab_release()
