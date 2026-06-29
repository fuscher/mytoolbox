"""gui/dialogs.py — Modal dialogs for MyToolbox.

All dialogs receive a Theme instance for consistent visual styling.
"""

from __future__ import annotations

import json
import os
import shutil
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk, messagebox
from typing import Dict, List, Optional

from core.scanner import scan_tools
from .theme import Theme, themed_text


def center_dialog(dialog: tk.Toplevel, parent: tk.Widget) -> None:
    dialog.update_idletasks()
    dw = dialog.winfo_width()
    dh = dialog.winfo_height()
    px = parent.winfo_x()
    py = parent.winfo_y()
    pw = parent.winfo_width()
    ph = parent.winfo_height()
    x = px + (pw - dw) // 2
    y = py + (ph - dh) // 2
    dialog.geometry(f"+{x}+{y}")


class AddToolDialog(tk.Toplevel):
    """Add-tool wizard dialog."""

    def __init__(self, parent: tk.Widget, config: dict, theme: Theme):
        super().__init__(parent)
        self.config = config
        self.t = theme
        self.result: Optional[str] = None
        self.transient(parent)
        self.title("添加工具")
        self.geometry("540x500")
        self.resizable(False, False)
        self.grab_set()
        self.configure(bg=theme.bg_root)

        self._categories: Dict[str, str] = {}
        self._load_categories()
        self._build_ui()

        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        center_dialog(self, parent)

    def _load_categories(self) -> None:
        tools_dir = self._tools_dir()
        cat_file = tools_dir / "_categories.json"
        if cat_file.exists():
            try:
                raw = json.loads(cat_file.read_text(encoding="utf-8"))
                self._categories = {k: v.get("display", k) for k, v in raw.items()}
            except (json.JSONDecodeError, OSError):
                pass

    def _next_cat_code(self) -> str:
        used = {int(c) for c in self._categories if c.isdigit()}
        for n in range(1, 1000):
            if n not in used:
                return f"{n:03d}"
        return "999"

    def _tools_dir(self) -> Path:
        td = self.config.get("tools_dir", "")
        if td:
            return Path(td).resolve()
        return Path(__file__).resolve().parent.parent / "tools"

    def _build_ui(self) -> None:
        t = self.t
        pad = {"padx": t.space_md, "pady": t.space_sm}

        # Content frame
        content = tk.Frame(self, bg=t.bg_root)
        content.pack(fill=tk.BOTH, expand=True, padx=t.space_xl, pady=t.space_lg)
        content.columnconfigure(1, weight=1)

        row = 0

        # ── File picker ────────────────────────────────────────────
        self._label(content, "安装包文件:", row, 0)
        file_frame = tk.Frame(content, bg=t.bg_root)
        file_frame.grid(row=row, column=1, sticky=tk.EW, **pad)
        self._files_var = tk.StringVar()
        ttk.Entry(file_frame, textvariable=self._files_var, state="readonly", width=34).pack(
            side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(file_frame, text="浏览...", command=self._browse_files).pack(side=tk.LEFT, padx=(t.space_sm, 0))
        row += 1

        # ── Category ───────────────────────────────────────────────
        self._label(content, "所属分类:", row, 0)
        cat_labels = [f"{v} ({k})" for k, v in self._categories.items()]
        self._cat_combo = ttk.Combobox(content, values=cat_labels, state="readonly", width=24)
        if cat_labels:
            self._cat_combo.current(0)
        self._cat_combo.grid(row=row, column=1, sticky=tk.EW, **pad)
        row += 1

        # ── Name ───────────────────────────────────────────────────
        self._label(content, "工具名称:", row, 0)
        self._name_var = tk.StringVar()
        ttk.Entry(content, textvariable=self._name_var, width=36).grid(row=row, column=1, sticky=tk.EW, **pad)
        row += 1

        # ── Version ────────────────────────────────────────────────
        self._label(content, "版本:", row, 0)
        self._ver_var = tk.StringVar()
        ttk.Entry(content, textvariable=self._ver_var, width=36).grid(row=row, column=1, sticky=tk.EW, **pad)
        row += 1

        # ── Description ────────────────────────────────────────────
        self._label(content, "描述:", row, 0, sticky=tk.NW)
        self._desc_text = themed_text(content, t, width=36, height=3)
        self._desc_text.grid(row=row, column=1, sticky=tk.EW, **pad)
        row += 1

        # ── Type (auto-detected) ───────────────────────────────────
        self._label(content, "类型:", row, 0)
        self._type_var = tk.StringVar(value="exe_installer")
        tk.Label(
            content, textvariable=self._type_var, bg=t.bg_root, fg=t.fg_disabled,
            font=(t.font_family, 9),
        ).grid(row=row, column=1, sticky=tk.W, **pad)
        row += 1

        # Separator
        tk.Frame(content, bg=t.border, height=1).grid(
            row=row, column=0, columnspan=2, sticky=tk.EW, pady=t.space_md)
        row += 1

        # ── Buttons ────────────────────────────────────────────────
        btn_frame = tk.Frame(content, bg=t.bg_root)
        btn_frame.grid(row=row, column=0, columnspan=2, sticky=tk.E)
        ttk.Button(btn_frame, text="确认添加", style="Accent.TButton",
                   command=self._on_confirm).pack(side=tk.LEFT, padx=(0, t.space_sm))
        ttk.Button(btn_frame, text="取消", command=self._on_cancel).pack(side=tk.LEFT)

    def _label(self, parent, text: str, row: int, col: int, **kw):
        t = self.t
        defaults = {"sticky": tk.W, "padx": t.space_md, "pady": t.space_sm}
        defaults.update(kw)
        tk.Label(
            parent, text=text, bg=t.bg_root, fg=t.fg_secondary,
            font=(t.font_family, 9),
        ).grid(row=row, column=col, **defaults)

    def _browse_files(self) -> None:
        files = filedialog.askopenfilenames(
            title="选择安装包文件",
            filetypes=[
                ("安装包", "*.exe *.msi *.zip"),
                ("所有文件", "*.*"),
            ],
        )
        if not files:
            return
        self._selected_files = list(files)
        names = [os.path.basename(f) for f in files]
        self._files_var.set("; ".join(names))
        first = Path(files[0]).stem
        for suffix in ["-Setup", "-setup", "-x64", "-x86", "-win64", "-win32",
                       "_Setup", "_setup", "_x64", "_x86"]:
            if first.lower().endswith(suffix.lower()):
                first = first[: -len(suffix)]
        if not self._name_var.get():
            self._name_var.set(first)
        ext = Path(files[0]).suffix.lower()
        type_map = {".exe": "exe_installer", ".msi": "msi_installer", ".zip": "archive"}
        self._type_var.set(type_map.get(ext, "exe_installer"))

    def _on_confirm(self) -> None:
        files = getattr(self, "_selected_files", [])
        if not files:
            messagebox.showwarning("提示", "请选择至少一个安装包文件。", parent=self)
            return
        name = self._name_var.get().strip()
        if not name:
            messagebox.showwarning("提示", "请输入工具名称。", parent=self)
            return
        cat_idx = self._cat_combo.current()
        cat_codes = list(self._categories.keys())
        cat_code = cat_codes[cat_idx] if 0 <= cat_idx < len(cat_codes) else None
        tools_dir = self._tools_dir()
        safe_name = "".join(c if c not in r'\/:*?"<>|' else "_" for c in name)
        if cat_code:
            target_dir = tools_dir / cat_code / safe_name
        else:
            target_dir = tools_dir / safe_name
        target_dir.mkdir(parents=True, exist_ok=True)
        installers = []
        for src in files:
            fname = os.path.basename(src)
            dst = target_dir / fname
            if not dst.exists():
                shutil.copy2(src, dst)
            label = Path(fname).stem
            installers.append({"file": fname, "label": label})
        info = {
            "name": name,
            "version": self._ver_var.get().strip() or None,
            "description": self._desc_text.get("1.0", tk.END).strip(),
            "installers": installers,
            "type": self._type_var.get(),
            "categories": [cat_code],
        }
        info_path = target_dir / "info.json"
        info_path.write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
        self.result = name
        self.destroy()

    def _on_cancel(self) -> None:
        self.result = None
        self.destroy()


# ═══════════════════════════════════════════════════════════════════════════
# Mini-dialogs
# ═══════════════════════════════════════════════════════════════════════════

class _NewCategoryDialog(tk.Toplevel):
    def __init__(self, parent: tk.Widget, theme: Theme):
        super().__init__(parent)
        self.t = theme
        self.result: Optional[str] = None
        self.transient(parent)
        self.title("新建分类")
        self.geometry("320x140")
        self.resizable(False, False)
        self.grab_set()
        self.configure(bg=theme.bg_root)

        tk.Label(
            self, text="分类名称:", bg=theme.bg_root, fg=theme.fg_secondary,
            font=(theme.font_family, 9),
        ).pack(padx=theme.space_md, pady=(theme.space_lg, theme.space_xs), anchor=tk.W)

        self._name_var = tk.StringVar()
        entry = ttk.Entry(self, textvariable=self._name_var, width=30)
        entry.pack(padx=theme.space_md, pady=theme.space_xs, fill=tk.X)
        entry.focus_set()
        entry.bind("<Return>", lambda e: self._ok())

        tk.Frame(self, bg=theme.bg_root).pack(pady=theme.space_md)
        btn = tk.Frame(self, bg=theme.bg_root)
        btn.pack(pady=(0, theme.space_md))
        ttk.Button(btn, text="确定", style="Accent.TButton", command=self._ok).pack(side=tk.LEFT, padx=(0, theme.space_sm))
        ttk.Button(btn, text="取消", command=self.destroy).pack(side=tk.LEFT)

        self.protocol("WM_DELETE_WINDOW", self.destroy)
        center_dialog(self, parent)

    def _ok(self) -> None:
        name = self._name_var.get().strip()
        if name:
            self.result = name
        self.destroy()


class _BatchCategorizeDialog(tk.Toplevel):
    def __init__(self, parent: tk.Widget, categories: List[tuple], theme: Theme):
        super().__init__(parent)
        self.t = theme
        self.result: Optional[str] = None
        self.transient(parent)
        self.title("批量分类")
        self.geometry("380x180")
        self.resizable(False, False)
        self.grab_set()
        self.configure(bg=theme.bg_root)

        tk.Label(
            self, text="选择目标分类:", bg=theme.bg_root, fg=theme.fg_secondary,
            font=(theme.font_family, 9),
        ).pack(padx=theme.space_md, pady=(theme.space_lg, theme.space_xs), anchor=tk.W)

        cat_labels = [f"{display} ({code})" for code, display in categories]
        self._cat_combo = ttk.Combobox(self, values=cat_labels, state="readonly", width=30)
        if cat_labels:
            self._cat_combo.current(0)
        self._cat_combo.pack(padx=theme.space_md, pady=theme.space_sm)
        self._cat_combo.focus_set()
        self._categories = categories

        tk.Frame(self, bg=theme.bg_root).pack(pady=theme.space_sm)
        btn = tk.Frame(self, bg=theme.bg_root)
        btn.pack()
        ttk.Button(btn, text="确定", style="Accent.TButton", command=self._ok).pack(side=tk.LEFT, padx=(0, theme.space_sm))
        ttk.Button(btn, text="取消", command=self.destroy).pack(side=tk.LEFT)

        self.protocol("WM_DELETE_WINDOW", self.destroy)
        center_dialog(self, parent)

    def _ok(self) -> None:
        idx = self._cat_combo.current()
        if 0 <= idx < len(self._categories):
            self.result = self._categories[idx][0]
        self.destroy()


class CategoryManageDialog(tk.Toplevel):
    def __init__(self, parent: tk.Widget, tools_dir: Path, theme: Theme):
        super().__init__(parent)
        self.t = theme
        self.result: bool = False
        self.transient(parent)
        self.title("分类管理")
        self.geometry("420x380")
        self.resizable(False, False)
        self.grab_set()
        self.configure(bg=theme.bg_root)

        self._tools_dir = tools_dir
        self._cat_file = tools_dir / "_categories.json"
        self._categories: Dict[str, str] = {}
        self._load_categories()

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        center_dialog(self, parent)

    def _load_categories(self) -> None:
        if self._cat_file.exists():
            try:
                raw = json.loads(self._cat_file.read_text(encoding="utf-8"))
                self._categories = {k: v.get("display", k) for k, v in raw.items()}
            except (json.JSONDecodeError, OSError):
                self._categories = {}

    def _save_categories(self) -> None:
        data = {k: {"display": v} for k, v in self._categories.items()}
        self._cat_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def _build_ui(self) -> None:
        t = self.t

        list_frame = tk.Frame(self, bg=t.bg_root)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=t.space_lg, pady=t.space_lg)

        tk.Label(
            list_frame, text="分类列表:", bg=t.bg_root, fg=t.fg_primary,
            font=(t.font_family, 10, "bold"),
        ).pack(anchor=tk.W, pady=(0, t.space_sm))

        from .theme import themed_listbox
        self._listbox = themed_listbox(list_frame, t, height=12)
        self._listbox.pack(fill=tk.BOTH, expand=True)
        self._refresh_list()

        # Separator
        tk.Frame(self, bg=t.border, height=1).pack(fill=tk.X, padx=t.space_lg)

        btn_frame = tk.Frame(self, bg=t.bg_root)
        btn_frame.pack(fill=tk.X, padx=t.space_lg, pady=(0, t.space_lg))

        ttk.Button(btn_frame, text="新增", style="Accent.TButton",
                   command=self._on_add).pack(side=tk.LEFT, padx=(0, t.space_sm))
        ttk.Button(btn_frame, text="编辑", command=self._on_edit).pack(side=tk.LEFT, padx=(0, t.space_sm))
        ttk.Button(btn_frame, text="删除", style="Danger.TButton",
                   command=self._on_delete).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text="关闭", command=self.destroy).pack(side=tk.RIGHT)

    def _refresh_list(self) -> None:
        self._listbox.delete(0, tk.END)
        for code, display in sorted(self._categories.items()):
            self._listbox.insert(tk.END, f"  {display}  ({code})")

    def _get_selected_code(self) -> Optional[str]:
        sel = self._listbox.curselection()
        if not sel:
            return None
        idx = sel[0]
        codes = sorted(self._categories.keys())
        if idx < len(codes):
            return codes[idx]
        return None

    def _on_add(self) -> None:
        dialog = _NewCategoryDialog(self, self.t)
        self.wait_window(dialog)
        if dialog.result:
            used = {int(k) for k in self._categories if k.isdigit()}
            for n in range(1, 1000):
                if n not in used:
                    code = f"{n:03d}"
                    break
            else:
                code = "999"
            self._categories[code] = dialog.result
            self._save_categories()
            self._refresh_list()

    def _on_edit(self) -> None:
        code = self._get_selected_code()
        if not code:
            messagebox.showwarning("提示", "请选择一个可编辑的分类", parent=self)
            return
        old_name = self._categories.get(code, "")
        dialog = _EditCategoryDialog(self, old_name, self.t)
        self.wait_window(dialog)
        if dialog.result:
            self._categories[code] = dialog.result
            self._save_categories()
            self._refresh_list()

    def _on_delete(self) -> None:
        code = self._get_selected_code()
        if not code:
            messagebox.showwarning("提示", "请选择一个可删除的分类", parent=self)
            return
        count = self._count_tools_in_category(code)
        if count > 0:
            result = messagebox.askyesno(
                "确认删除",
                f"该分类下有 {count} 个工具，删除后这些工具将不再属于任何分类，确定继续？",
                parent=self,
            )
            if not result:
                return
        del self._categories[code]
        self._save_categories()
        if count > 0:
            from core.index_manager import IndexManager
            manager = IndexManager(self._tools_dir)
            all_tools = manager.get_all_tools()
            for tool in all_tools:
                cats = tool.get("categories", [])
                if code in cats:
                    cats.remove(code)
                    manager.update_tool(tool["id"], {"categories": cats})
        self._refresh_list()

    def _count_tools_in_category(self, code: str) -> int:
        try:
            from core.index_manager import IndexManager
            manager = IndexManager(self._tools_dir)
            return len(manager.get_tools_by_category(code))
        except Exception:
            return 0


class _EditCategoryDialog(tk.Toplevel):
    def __init__(self, parent: tk.Widget, current_name: str, theme: Theme):
        super().__init__(parent)
        self.t = theme
        self.result: Optional[str] = None
        self.transient(parent)
        self.title("编辑分类")
        self.geometry("320x140")
        self.resizable(False, False)
        self.grab_set()
        self.configure(bg=theme.bg_root)

        tk.Label(
            self, text="分类名称:", bg=theme.bg_root, fg=theme.fg_secondary,
            font=(theme.font_family, 9),
        ).pack(padx=theme.space_md, pady=(theme.space_lg, theme.space_xs), anchor=tk.W)

        self._name_var = tk.StringVar(value=current_name)
        entry = ttk.Entry(self, textvariable=self._name_var, width=30)
        entry.pack(padx=theme.space_md, pady=theme.space_xs, fill=tk.X)
        entry.focus_set()
        entry.select_range(0, tk.END)
        entry.bind("<Return>", lambda e: self._ok())

        tk.Frame(self, bg=theme.bg_root).pack(pady=theme.space_md)
        btn = tk.Frame(self, bg=theme.bg_root)
        btn.pack()
        ttk.Button(btn, text="确定", style="Accent.TButton", command=self._ok).pack(side=tk.LEFT, padx=(0, theme.space_sm))
        ttk.Button(btn, text="取消", command=self.destroy).pack(side=tk.LEFT)

        self.protocol("WM_DELETE_WINDOW", self.destroy)
        center_dialog(self, parent)

    def _ok(self) -> None:
        name = self._name_var.get().strip()
        if name:
            self.result = name
        self.destroy()
