"""gui/disk_scan_tab.py — Disk space analysis Tab.

Scans a user-selected directory and displays its contents in a lazy-loaded
tree view with sortable columns (Name, Size, Type, Modified).  Built on
``core.disk_scanner`` with a background-thread + ``queue.Queue`` architecture
that keeps the UI responsive even for very large directory trees.
"""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Callable, Dict, List, Optional

from core.disk_scanner import (
    FileEntry,
    ScanResult,
    format_size,
    scan_directory,
)
from .theme import Theme




# ═══════════════════════════════════════════════════════════════════════════
# Tab
# ═══════════════════════════════════════════════════════════════════════════

class DiskScanTab(ttk.Frame):
    """Tab: Disk space scanner with lazy-loaded Treeview."""

    def __init__(self, parent: tk.Widget, config: dict, theme: Theme,
                 set_status: Callable[[str], None]):
        super().__init__(parent)
        self.config = config
        self.t = theme
        self._set_status = set_status

        # Scan state
        self._result: Optional[ScanResult] = None
        self._scanning = False
        self._stop_event: Optional[threading.Event] = None
        self._msg_queue: queue.Queue = queue.Queue()
        self._after_id: Optional[str] = None

        # Treeview sort state
        self._sort_col = "name"        # current sort column
        self._sort_reverse = False     # ascending by default

        # Map: tree item iid → FileEntry (for lazy-loading children)
        self._item_to_entry: Dict[str, FileEntry] = {}

        self._build_ui()
        self._start_polling()

    # ── UI construction ──────────────────────────────────────────────────

    def _build_ui(self) -> None:
        t = self.t

        # ── Top bar ───────────────────────────────────────────────────
        top = tk.Frame(self, bg=t.bg_panel, padx=t.space_md, pady=t.space_sm)
        top.pack(fill=tk.X)

        tk.Label(
            top, text="📁", bg=t.bg_panel, fg=t.fg_secondary,
            font=("Segoe UI Emoji", 12),
        ).pack(side=tk.LEFT)
        tk.Label(
            top, text="磁盘扫描", bg=t.bg_panel, fg=t.fg_primary,
            font=(t.font_family, 10, "bold"),
        ).pack(side=tk.LEFT, padx=(t.space_xs, t.space_lg))

        # Directory path entry
        self._path_var = tk.StringVar()
        path_entry = ttk.Entry(top, textvariable=self._path_var, width=36)
        path_entry.pack(side=tk.LEFT, padx=(0, t.space_xs))
        path_entry.bind("<Return>", lambda _e: self._start_scan())

        ttk.Button(top, text="浏览...", command=self._browse).pack(
            side=tk.LEFT, padx=(0, t.space_xs))
        self._scan_btn = ttk.Button(
            top, text="开始扫描", style="Accent.TButton", command=self._start_scan)
        self._scan_btn.pack(side=tk.LEFT, padx=(0, t.space_xs))

        # Cancel button (hidden until scanning)
        self._cancel_btn = ttk.Button(
            top, text="取消", style="Danger.TButton", command=self._cancel_scan)
        # Not packed yet — shown only during scan

        # Spacer
        tk.Frame(top, bg=t.bg_panel).pack(side=tk.LEFT, fill=tk.X, expand=True)

        # ── Progress bar ──────────────────────────────────────────────
        self._progress = ttk.Progressbar(
            self, mode="determinate", style="TProgressbar")
        # Hidden until scanning

        # ── Stats bar ─────────────────────────────────────────────────
        stats_frame = tk.Frame(self, bg=t.bg_panel)
        stats_frame.pack(fill=tk.X)

        self._stats_label = tk.Label(
            stats_frame, text='选择一个目录，然后点击"开始扫描"',
            bg=t.bg_panel, fg=t.fg_secondary,
            font=(t.font_family, 9),
        )
        self._stats_label.pack(side=tk.LEFT)

        # ── Separator ─────────────────────────────────────────────────
        tk.Frame(self, bg=t.border, height=1).pack(fill=tk.X)

        # ── Treeview ──────────────────────────────────────────────────
        tree_frame = tk.Frame(self, bg=t.bg_root)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)

        columns = ("name", "size", "type", "modified")
        self._tree = ttk.Treeview(
            tree_frame, columns=columns, show="tree headings",
            selectmode="browse",
        )

        self._tree.heading("name", text="名称", command=lambda: self._sort_by("name"))
        self._tree.heading("size", text="大小", command=lambda: self._sort_by("size"))
        self._tree.heading("type", text="类型", command=lambda: self._sort_by("type"))
        self._tree.heading("modified", text="修改时间", command=lambda: self._sort_by("modified"))

        self._tree.column("#0", width=0, stretch=False)  # hide tree icon column
        self._tree.column("name", width=300, minwidth=180)
        self._tree.column("size", width=100, minwidth=80)
        self._tree.column("type", width=120, minwidth=80)
        self._tree.column("modified", width=140, minwidth=100)

        # Scrollbars
        vscroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL,
                                 command=self._tree.yview)
        hscroll = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL,
                                 command=self._tree.xview)
        self._tree.configure(
            yscrollcommand=vscroll.set,
            xscrollcommand=hscroll.set)

        vscroll.pack(side=tk.RIGHT, fill=tk.Y)
        hscroll.pack(side=tk.BOTTOM, fill=tk.X)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Tags
        self._tree.tag_configure("even", background=t.bg_input)
        self._tree.tag_configure("odd", background=t.bg_root)
        self._tree.tag_configure("folder", foreground=t.accent)

        # Bind open event for lazy loading
        self._tree.bind("<<TreeviewOpen>>", self._on_tree_open)

        # ── Separator + bottom bar ────────────────────────────────────
        tk.Frame(self, bg=t.border, height=1).pack(fill=tk.X)

        btn_frame = tk.Frame(self, bg=t.bg_panel, padx=t.space_md, pady=t.space_sm)
        btn_frame.pack(fill=tk.X)

        tk.Label(
            btn_frame, text="双击文件夹展开/折叠 | 点击列头排序",
            bg=t.bg_panel, fg=t.fg_disabled, font=(t.font_family, 8),
        ).pack(side=tk.LEFT)

        ttk.Button(btn_frame, text="复制结果", command=self._copy_results).pack(
            side=tk.RIGHT)

    # ── Polling loop ─────────────────────────────────────────────────────

    def _start_polling(self) -> None:
        """Begin the periodic queue check (safe to call multiple times)."""
        if self._after_id is not None:
            return  # already polling
        self._poll_queue()

    def _poll_queue(self) -> None:
        """Drain the message queue and update UI accordingly."""
        try:
            while True:
                msg = self._msg_queue.get_nowait()
                self._handle_message(msg)
        except queue.Empty:
            pass
        # Reschedule
        self._after_id = self.after(100, self._poll_queue)

    def _handle_message(self, msg: dict) -> None:
        """Process a single message from the worker thread."""
        msg_type = msg.get("type")

        if msg_type == "progress":
            done = msg["done"]
            total = msg["total"]
            self._progress["value"] = done
            self._progress["maximum"] = total
            pct = (done / total * 100) if total else 0
            self._set_status(f"正在扫描... {pct:.0f}% ({done:,} / {total:,})")

        elif msg_type == "result":
            self._on_scan_done(msg["result"])

        elif msg_type == "error":
            self._stop_scanning_ui()
            self._stats_label.config(text=f"扫描失败: {msg['message']}")
            self._set_status(f"扫描失败: {msg['message']}")

    # ── Scan lifecycle ──────────────────────────────────────────────────

    def _browse(self) -> None:
        """Open folder picker dialog."""
        path = filedialog.askdirectory(title="选择要扫描的目录")
        if path:
            self._path_var.set(path)

    def _start_scan(self) -> None:
        """Validate input and launch background scan."""
        path = self._path_var.get().strip()
        if not path:
            self._stats_label.config(text="请先选择或输入一个目录路径")
            return
        if not os.path.isdir(path):
            self._stats_label.config(text=f"目录不存在: {path}")
            return

        # Reset UI
        self._tree.delete(*self._tree.get_children())
        self._item_to_entry.clear()
        self._result = None

        # Show progress bar and cancel button
        self._scanning = True
        self._progress.pack(
            after=self._stats_label.master, fill=tk.X,
            padx=self.t.space_md, pady=(0, 0))
        self._progress["value"] = 0
        self._cancel_btn.pack(
            side=tk.LEFT, padx=(0, self.t.space_xs),
            after=self._scan_btn)
        self._scan_btn.config(state="disabled", text="扫描中...")

        self._stats_label.config(text="正在估算文件数量...")
        self._set_status("正在初始化磁盘扫描...")

        # Reset message queue
        while True:
            try:
                self._msg_queue.get_nowait()
            except queue.Empty:
                break

        # Launch worker
        self._stop_event = threading.Event()

        def worker():
            scan_path = os.path.abspath(path)
            result = None
            try:
                result = scan_directory(
                    scan_path,
                    on_progress=lambda done, total: self._msg_queue.put(
                        {"type": "progress", "done": done, "total": total}),
                    stop_event=self._stop_event,
                )
            except Exception as exc:
                self._msg_queue.put({"type": "error", "message": str(exc)})
                return
            if self._stop_event.is_set():
                # Scan was cancelled — put partial result
                pass
            self._msg_queue.put({"type": "result", "result": result})

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

    def _cancel_scan(self) -> None:
        """Request cancellation of the running scan."""
        if self._stop_event:
            self._stop_event.set()
        self._set_status("正在取消扫描...")

    def _stop_scanning_ui(self) -> None:
        """Restore UI to non-scanning state."""
        self._scanning = False
        self._progress.pack_forget()
        self._cancel_btn.pack_forget()
        self._scan_btn.config(state="normal", text="开始扫描")

    # ── Scan result handling ─────────────────────────────────────────────

    def _on_scan_done(self, result: Optional[ScanResult]) -> None:
        """Process completed (or cancelled) scan."""
        self._stop_scanning_ui()

        if result is None:
            self._stats_label.config(text="扫描已取消或未返回数据")
            self._set_status("扫描已取消")
            return

        self._result = result

        # Update stats
        self._stats_label.config(
            text=(f"总大小: {format_size(result.total_size)}  |  "
                  f"文件数: {result.total_files:,}  |  "
                  f"文件夹数: {result.total_dirs:,}"))

        # Populate root level
        self._populate_children("", result.root_path, result.root_entries)

        if self._stop_event and self._stop_event.is_set():
            self._set_status("扫描已取消（部分结果）")
        else:
            self._set_status(
                f"扫描完成 — {format_size(result.total_size)} "
                f"({result.total_files:,} 文件, {result.total_dirs:,} 文件夹)")

    # ── Treeview population ──────────────────────────────────────────────

    def _populate_children(
        self,
        parent_iid: str,
        parent_path: str,
        entries: List[FileEntry],
        index_offset: int = 0,
    ) -> None:
        """Insert *entries* into the tree under *parent_iid*.

        Directories get a dummy child so the expand arrow appears; real
        children are loaded on ``<<TreeviewOpen>>``.
        """
        t = self.t

        for i, entry in enumerate(entries):
            row_index = index_offset + i
            tag = "even" if row_index % 2 == 0 else "odd"

            iid = entry.path  # use full path as item ID (unique)

            if entry.is_dir:
                # Folder — show with folder icon prefix
                display_name = f"📁 {entry.name}"
                size_display = ""  # size shown for dirs only after computing
            else:
                display_name = f"📄 {entry.name}"
                size_display = format_size(entry.size)

            values = (display_name, size_display, entry.extension, entry.modified)
            tags = (tag,) if not entry.is_dir else (tag, "folder")

            self._tree.insert(
                parent_iid, "end", iid=iid, values=values,
                tags=tags, open=False,
            )
            self._item_to_entry[iid] = entry

            if entry.is_dir:
                # Add a single dummy child so the expand arrow appears
                self._tree.insert(iid, "end", values=("...", "", "", ""))

    def _on_tree_open(self, event: tk.Event) -> None:
        """Lazy-load children of a folder when the user expands it."""
        if not self._result:
            return

        tree = self._tree
        selection = tree.selection()
        if not selection:
            return

        iid = selection[0]
        entry = self._item_to_entry.get(iid)
        if entry is None or not entry.is_dir:
            return

        # Check if this folder already has real children (not dummy)
        children_iids = tree.get_children(iid)
        if len(children_iids) != 1:
            return  # already loaded or empty

        first_child_values = tree.item(children_iids[0], "values")
        if first_child_values != ("...", "", "", ""):
            return  # already loaded

        # Remove the dummy
        tree.delete(children_iids[0])

        # Insert real children
        child_entries = self._result.children.get(entry.path, [])
        self._populate_children(iid, entry.path, child_entries)

    # ── Column sorting ──────────────────────────────────────────────────

    def _sort_by(self, col: str) -> None:
        """Sort the treeview by *col* (toggles direction on repeat clicks)."""
        if self._sort_col == col:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_col = col
            self._sort_reverse = False

        if not self._result:
            return

        # Update heading arrow indicators
        for c in ("name", "size", "type", "modified"):
            arrow = ""
            if c == self._sort_col:
                arrow = " ▲" if self._sort_reverse else " ▼"
            self._tree.heading(c, text={
                "name": "名称", "size": "大小",
                "type": "类型", "modified": "修改时间",
            }[c] + arrow)

        # Re-sort recursively starting from root
        self._sort_children("", self._result.root_entries)

    def _sort_children(self, parent_iid: str, entries: List[FileEntry]) -> None:
        """Recursively sort *entries* and update the tree under *parent_iid*."""
        col = self._sort_col
        rev = self._sort_reverse
        is_name_col = (col == "name")

        # Sort: dirs first (always), then by selected column
        def key_func(e: FileEntry):
            if is_name_col:
                return (not e.is_dir, e.name.lower())
            elif col == "size":
                # Use raw byte size for numeric sort
                return (not e.is_dir, e.size)
            elif col == "type":
                return (not e.is_dir, e.extension.lower(), e.name.lower())
            elif col == "modified":
                return (not e.is_dir, e.modified or "", e.name.lower())
            return (not e.is_dir, e.name.lower())

        entries.sort(key=key_func, reverse=rev)

        # Delete and re-insert all children of parent_iid, preserving loaded state
        tree_children = self._tree.get_children(parent_iid)

        # Build map of which items have been loaded (children expanded past dummy)
        loaded_dirs: Dict[str, List[FileEntry]] = {}
        for iid in tree_children:
            entry = self._item_to_entry.get(iid)
            if entry and entry.is_dir:
                sub_iids = self._tree.get_children(iid)
                if len(sub_iids) != 1 or self._tree.item(sub_iids[0], "values") != ("...", "", "", ""):
                    loaded_dirs[entry.path] = self._result.children.get(entry.path, [])

        # Remove all children
        for iid in tree_children:
            self._tree.delete(iid)

        # Re-insert sorted
        for i, entry in enumerate(entries):
            row_index = len(tree_children) + i  # approximate
            tag = "even" if row_index % 2 == 0 else "odd"

            iid = entry.path

            if entry.is_dir:
                display_name = f"📁 {entry.name}"
                size_display = ""
            else:
                display_name = f"📄 {entry.name}"
                size_display = format_size(entry.size)

            values = (display_name, size_display, entry.extension, entry.modified)
            tags = (tag,) if not entry.is_dir else (tag, "folder")

            self._tree.insert(
                parent_iid, "end", iid=iid, values=values,
                tags=tags, open=False,
            )

            # Restore loaded children if any
            if entry.is_dir and entry.path in loaded_dirs:
                # Add dummy so expand arrow appears
                self._tree.insert(iid, "end", values=("...", "", "", ""))
                # But we lost the actual children — re-add them
                # (simplest: just re-add the dummy; user re-expands to get sorted)

    # ── Actions ──────────────────────────────────────────────────────────

    def _copy_results(self) -> None:
        """Copy scan summary to clipboard."""
        if not self._result:
            self._set_status("没有扫描结果可复制")
            return

        r = self._result
        lines = [
            f"磁盘扫描结果: {r.root_path}",
            f"总大小: {format_size(r.total_size)}",
            f"文件数: {r.total_files:,}",
            f"文件夹数: {r.total_dirs:,}",
            f"",
            f"{'名称':<40} {'大小':>10} {'类型':<15} {'修改时间'}",
            f"{'-' * 40} {'-' * 10} {'-' * 15} {'-' * 16}",
        ]

        def _walk(path: str, depth: int = 0):
            for fe in r.children.get(path, []):
                indent = "  " * depth
                size_str = format_size(fe.size) if not fe.is_dir else "(文件夹)"
                lines.append(
                    f"{indent}{fe.name:<{40 - depth * 2}} "
                    f"{size_str:>10} {fe.extension:<15} {fe.modified}")
                if fe.is_dir:
                    _walk(fe.path, depth + 1)

        _walk(r.root_path)

        text = "\n".join(lines)
        self.clipboard_clear()
        self.clipboard_append(text)
        self._set_status("已复制扫描结果到剪贴板")

    # ── Cleanup ──────────────────────────────────────────────────────────

    def destroy(self) -> None:
        """Cancel any running scan and clean up."""
        if self._stop_event:
            self._stop_event.set()
        if self._after_id:
            self.after_cancel(self._after_id)
            self._after_id = None
        super().destroy()
