"""gui/install_tab.py — Category sidebar + tool card grid with modern styling.

Each tool card shows:
  icon · name · version dropdown (if multi-version) · Install / Installed button
"""

from __future__ import annotations

import json
import os
import tkinter as tk
from pathlib import Path
from tkinter import ttk, messagebox
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


CARD_W = 200
CARD_H = 226
CARD_PAD_X = 10
CARD_PAD_Y = 10
ICON_SIZE = 68
GRID_COLUMNS = 4

_INSTALLER_EXTENSIONS = {".exe", ".msi", ".msu", ".zip", ".7z", ".rar"}


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

        # ── Right content area ──────────────────────────────────────────
        right = tk.Frame(self, bg=t.bg_root)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Top bar
        top_bar = tk.Frame(right, bg=t.bg_root)
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
        tk.Frame(right, bg=t.border, height=1).pack(fill=tk.X, padx=t.space_md)

        # Card canvas
        content_frame = tk.Frame(right, bg=t.bg_canvas)
        content_frame.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)

        self._canvas = themed_canvas(content_frame, t)
        self._vscroll = ttk.Scrollbar(content_frame, orient=tk.VERTICAL, command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._vscroll.set)

        self._vscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Batch action bar (hidden by default)
        self._batch_frame = tk.Frame(right, bg=t.bg_panel, height=60)
        self._batch_frame.pack(side=tk.BOTTOM, fill=tk.X)
        self._batch_frame.pack_forget()
        self._batch_frame.pack_propagate(False)

        # Grid frame inside canvas
        self._grid_frame = tk.Frame(self._canvas, bg=t.bg_canvas)
        self._canvas_window = self._canvas.create_window(
            (0, 0), window=self._grid_frame, anchor=tk.NW
        )

        self._grid_frame.bind("<Configure>", self._on_grid_resize)
        self._canvas.bind("<Configure>", self._on_canvas_resize)
        self._canvas.bind("<MouseWheel>", self._on_mousewheel)

    def _on_mousewheel(self, event) -> None:
        self._canvas.yview_scroll(-event.delta // 60, "units")

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
        result = messagebox.askyesno(
            "发现新安装包",
            f"检测到 {len(new_files)} 个新安装包，是否导入？\n\n"
            + "\n".join(f"  - {f[0].name}" for f in new_files[:5])
            + ("\n  ..." if len(new_files) > 5 else ""),
            parent=self,
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
                messagebox.showwarning("不支持的文件类型", f"{src_path.name} 不是支持的安装包格式")
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
            result = messagebox.askyesno(
                "确认删除",
                f"以下工具正在运行中，确定要删除吗？\n\n" + "\n".join(f"  - {name}" for name in running_tools),
                parent=self,
            )
            if not result:
                return
        msg = f"确定要删除选中的 {len(self._selected_tools)} 个工具吗？"
        if all_file_paths:
            file_list = "\n".join(f"  - {p.name}" for p in all_file_paths[:10])
            if len(all_file_paths) > 10:
                file_list += f"\n  ... 等共 {len(all_file_paths)} 个文件"
            msg += "\n\n将删除以下文件：\n" + file_list
        result = messagebox.askyesno("确认删除", msg, parent=self)
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
        self.refresh()
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
            result = messagebox.askyesno(
                "确认移除",
                f"该工具 {tool.get('name', '')} 正在运行中，确定要移除吗？",
                parent=self,
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
        result = messagebox.askyesno("确认移除", msg, parent=self)
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
        self.refresh()
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

        state = load_state(self.config)

        col, row = 0, 0
        for key in sorted(tools.keys()):
            tool = tools[key]
            card = self._make_card(self._grid_frame, key, tool, state)
            card.grid(row=row, column=col, padx=CARD_PAD_X // 2, pady=CARD_PAD_Y // 2, sticky=tk.N)
            self._card_frames.append(card)
            col += 1
            if col >= GRID_COLUMNS:
                col = 0
                row += 1

    def _make_card(self, parent: tk.Widget, key: str, tool: ToolInfo, state: dict) -> tk.Frame:
        t = self.t

        # Card frame — tk.Frame for full bg colour control
        card = tk.Frame(parent, width=CARD_W, height=CARD_H, bg=t.bg_card,
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

        # Icon background circle (decorative)
        icon_container = tk.Canvas(
            card, width=ICON_SIZE + 12, height=ICON_SIZE + 12,
            bg=t.bg_card, highlightthickness=0,
        )
        icon_container.pack(pady=(t.space_md, t.space_xs))
        icon_container.create_oval(
            2, 2, ICON_SIZE + 10, ICON_SIZE + 10,
            fill=t.bg_input, outline="",
        )

        try:
            img = tk.PhotoImage(file=str(icon_path))
            iw, ih = img.width(), img.height()
            if iw > ICON_SIZE or ih > ICON_SIZE:
                factor_x = max(1, iw // ICON_SIZE)
                factor_y = max(1, ih // ICON_SIZE)
                img = img.subsample(factor_x, factor_y)
            self._photo_refs.append(img)
            icon_container.create_image((ICON_SIZE + 12) // 2, (ICON_SIZE + 12) // 2, image=img)
        except tk.TclError:
            icon_container.create_text(
                (ICON_SIZE + 12) // 2, (ICON_SIZE + 12) // 2,
                text="📦", font=("Segoe UI Emoji", 28), fill=t.fg_disabled,
            )

        # ── Remove button (subtle) ──────────────────────────────────
        remove_btn = tk.Label(
            card, text="✕", bg=t.bg_card, fg=t.fg_disabled,
            font=(t.font_family, 10), cursor="hand2",
        )
        remove_btn.place(x=CARD_W - 26, y=t.space_xs)
        remove_btn.bind("<Button-1>", lambda e, k=key, tl=tool: self._on_remove_tool(k, tl))
        remove_btn.bind("<Enter>", lambda e, lbl=remove_btn: lbl.configure(fg=t.danger))
        remove_btn.bind("<Leave>", lambda e, lbl=remove_btn: lbl.configure(fg=t.fg_disabled))

        # ── Name ────────────────────────────────────────────────────
        name = tool.get("name") or tool["folder_name"]
        name_label = tk.Label(
            card, text=name, bg=t.bg_card, fg=t.fg_primary,
            font=(t.font_family, 9, "bold"), wraplength=CARD_W - 20, justify=tk.CENTER,
        )
        name_label.pack(pady=(0, 2))

        # ── Version ─────────────────────────────────────────────────
        installers = tool.get("installers", [])
        version_var = tk.IntVar(value=0)

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

        # ── Action button ───────────────────────────────────────────
        installed = is_installed(key, self.config)
        archive_extensions = {".zip", ".7z", ".rar"}
        is_archive = False
        if installers:
            installer_file = installers[0].get("file", "")
            ext = Path(installer_file).suffix.lower()
            is_archive = ext in archive_extensions

        if installed:
            btn_text = "✔ 已安装"
            btn_style = "TButton"
            btn_state = tk.DISABLED
        elif is_archive:
            btn_text = "打开"
            btn_style = "TButton"
            btn_state = tk.NORMAL
        elif installers:
            btn_text = "安装"
            btn_style = "Accent.TButton"
            btn_state = tk.NORMAL
        else:
            btn_text = "无安装包"
            btn_style = "TButton"
            btn_state = tk.DISABLED

        btn = ttk.Button(
            card, text=btn_text, style=btn_style,
            command=lambda k=key, tl=tool, vv=version_var: self._on_install(k, tl, vv),
        )
        if btn_state == tk.DISABLED:
            btn.state(["disabled"])
        btn.pack(pady=(6, t.space_md))

        # Hover effect on card border
        def _on_enter(e, c=card):
            c.configure(highlightbackground=t.border_focus)
        def _on_leave(e, c=card):
            c.configure(highlightbackground=t.border)
        card.bind("<Enter>", _on_enter)
        card.bind("<Leave>", _on_leave)
        # Propagate to children so hover works on any part of the card
        for child in card.winfo_children():
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

    def _on_canvas_resize(self, event) -> None:
        self._canvas.itemconfig(self._canvas_window, width=event.width)

    def destroy(self) -> None:
        if self._watcher and self._watchdog_imported:
            try:
                self._observer.stop()
                self._observer.join()
            except Exception:
                pass
        super().destroy()
