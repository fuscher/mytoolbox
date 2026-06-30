"""gui/hardware_tab.py — Hardware information display Tab.

Shows detailed hardware specs (CPU, memory, GPU, motherboard, disks,
network adapters, OS) in a Treeview grid, collected via WMIC and ctypes
without any third-party dependencies.
"""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk
from typing import Callable, List, Optional

from core.hardware_info import collect_hardware, HardwareProfile
from .theme import Theme


class HardwareTab(ttk.Frame):
    """Tab: Hardware information overview."""

    def __init__(self, parent: tk.Widget, config: dict, theme: Theme,
                 set_status: Callable[[str], None]):
        super().__init__(parent)
        self.config = config
        self.t = theme
        self._set_status = set_status
        self._profile: Optional[HardwareProfile] = None
        self._rows: List[tuple] = []  # (category, name, value)

        self._build_ui()
        self._start_scan()

    # ── UI ────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        t = self.t

        # Top bar
        top = tk.Frame(self, bg=t.bg_panel, padx=t.space_md, pady=t.space_sm)
        top.pack(fill=tk.X)

        tk.Label(
            top, text="🖥", bg=t.bg_panel, fg=t.fg_secondary,
            font=("Segoe UI Emoji", 12),
        ).pack(side=tk.LEFT)
        tk.Label(
            top, text="硬件信息", bg=t.bg_panel, fg=t.fg_primary,
            font=(t.font_family, 10, "bold"),
        ).pack(side=tk.LEFT, padx=(t.space_xs, t.space_lg))

        # Info label
        self._info_label = tk.Label(
            top, text="正在扫描...", bg=t.bg_panel, fg=t.fg_secondary,
            font=(t.font_family, 9),
        )
        self._info_label.pack(side=tk.LEFT)

        # Spacer + refresh
        tk.Frame(top, bg=t.bg_panel).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(top, text="刷新", command=self._refresh).pack(
            side=tk.RIGHT, padx=(0, t.space_xs))

        # Separator
        tk.Frame(self, bg=t.border, height=1).pack(fill=tk.X)

        # Treeview
        list_frame = tk.Frame(self, bg=t.bg_root)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)

        columns = ("category", "name", "value")
        self._tree = ttk.Treeview(
            list_frame, columns=columns, show="headings", selectmode="browse",
        )

        self._tree.heading("category", text="类别")
        self._tree.heading("name", text="属性")
        self._tree.heading("value", text="值")

        self._tree.column("category", width=70,  minwidth=60, stretch=False)
        self._tree.column("name",     width=160, minwidth=120)
        self._tree.column("value",    width=400, minwidth=200)

        vscroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL,
                                command=self._tree.yview)
        self._tree.configure(yscrollcommand=vscroll.set)

        vscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Zebra striping
        self._tree.tag_configure("even", background=t.bg_input)
        self._tree.tag_configure("odd",  background=t.bg_root)

        # Category group tags (subtle highlight)
        self._tree.tag_configure("cat_header",
                                background=t.bg_selected,
                                font=(t.font_family, 9, "bold"))

        # Separator + bottom bar
        tk.Frame(self, bg=t.border, height=1).pack(fill=tk.X)

        btn_frame = tk.Frame(self, bg=t.bg_panel, padx=t.space_md, pady=t.space_sm)
        btn_frame.pack(fill=tk.X)

        tk.Label(
            btn_frame, text="数据来源: WMIC + kernel32 | 不需要第三方包",
            bg=t.bg_panel, fg=t.fg_disabled, font=(t.font_family, 8),
        ).pack(side=tk.LEFT)

        ttk.Button(btn_frame, text="复制全部", command=self._copy_all).pack(
            side=tk.RIGHT)

    # ── Data ──────────────────────────────────────────────────────────

    def _start_scan(self) -> None:
        self._set_status("正在扫描硬件信息...")
        self._tree.delete(*self._tree.get_children())
        self._tree.insert("", "end", values=(
            "状态", "扫描中", "正在通过 WMIC 读取硬件信息，请稍候..."))
        self._info_label.config(text="正在扫描...")

        def worker():
            profile = collect_hardware(
                lambda step: self._safe_status(f"正在扫描硬件: {step}"))
            self._safe_populate(profile)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

    def _safe_status(self, msg: str) -> None:
        """Schedule a status update from a background thread."""
        try:
            self.after(0, lambda: self._set_status(msg))
        except RuntimeError:
            pass  # app destroyed mid-scan

    def _safe_populate(self, profile: HardwareProfile) -> None:
        """Schedule final population from a background thread."""
        try:
            self.after(0, lambda: self._on_scan_done(profile))
        except RuntimeError:
            pass

    def _refresh(self) -> None:
        self._start_scan()

    def _on_scan_done(self, profile: HardwareProfile) -> None:
        self._profile = profile
        self._build_rows()
        self._rebuild_tree()
        self._info_label.config(text=f"CPU: {profile.cpu.name.split()[0] if profile.cpu.name else 'N/A'}  |  "
                                f"内存: {profile.memory.total_gb} GB  |  "
                                f"OS: {profile.os_name}")
        self._set_status("硬件信息扫描完毕")

    def _build_rows(self) -> None:
        """Convert HardwareProfile into flat (category, name, value) tuples."""
        p = self._profile
        if not p:
            self._rows = []
            return

        rows: List[tuple] = []

        def add(cat: str, name: str, value: str):
            rows.append((cat, name, value))

        # ── CPU ─────────────────────────────────────────────────────
        cpu = p.cpu
        add("CPU", "处理器", cpu.name)
        add("CPU", "核心数", f"{cpu.cores} 物理核心")
        add("CPU", "线程数", f"{cpu.logical_processors} 逻辑线程")
        add("CPU", "最大频率", f"{cpu.max_clock_mhz} MHz")
        if cpu.l2_cache_kb:
            add("CPU", "L2 缓存", f"{cpu.l2_cache_kb} KB")
        if cpu.l3_cache_kb:
            add("CPU", "L3 缓存", f"{cpu.l3_cache_kb // 1024} MB")

        # ── Memory ──────────────────────────────────────────────────
        mem = p.memory
        add("内存", "总容量", f"{mem.total_gb} GB")
        add("内存", "可用", f"{mem.available_gb} GB")
        add("内存", "占用率", f"{mem.load_percent}%")
        for i, stick in enumerate(mem.sticks):
            label = f"插槽 #{i + 1}" if len(mem.sticks) > 1 else "内存条"
            add("内存", label,
                f"{stick.capacity_gb} GB  {stick.speed_mhz} MHz  [{stick.part_number}]")

        # ── GPU ─────────────────────────────────────────────────────
        for i, gpu in enumerate(p.gpus):
            tag = f"GPU #{i + 1}" if len(p.gpus) > 1 else "GPU"
            add("GPU", tag, gpu.name)
            if gpu.vram_mb:
                add("GPU", "显存", f"{gpu.vram_mb} MB")
            if gpu.driver_version:
                add("GPU", "驱动版本", gpu.driver_version)

        # ── Motherboard ─────────────────────────────────────────────
        mb = p.motherboard
        if mb.manufacturer or mb.product:
            add("主板", "型号", f"{mb.manufacturer} {mb.product}".strip())
        if mb.bios_vendor:
            add("BIOS", "厂商", mb.bios_vendor)
        if mb.bios_version:
            add("BIOS", "版本", mb.bios_version)

        # ── Disks ───────────────────────────────────────────────────
        for d in p.disks:
            add("磁盘", d.model, f"{d.size_gb} GB  ({d.media_type})")

        # ── Network ─────────────────────────────────────────────────
        for n in p.networks:
            speed = f"{n.speed_mbps} Mbps" if n.speed_mbps else "—"
            add("网络", n.name, f"MAC={n.mac}  {speed}")

        # ── OS ──────────────────────────────────────────────────────
        add("操作系统", "系统", p.os_name)
        add("操作系统", "构建版本", p.os_build)
        # Detect architecture via platform module
        import platform as _platform
        arch = _platform.architecture()[0] if hasattr(_platform, 'architecture') else 'x64'
        add("操作系统", "架构", arch)

        self._rows = rows

    def _rebuild_tree(self) -> None:
        self._tree.delete(*self._tree.get_children())

        if not self._rows:
            self._tree.insert("", "end", values=(
                "错误", "无数据", "未能获取硬件信息"))
            return

        last_cat = None
        for i, (cat, name, value) in enumerate(self._rows):
            tag = "even" if i % 2 == 0 else "odd"
            display_cat = cat if cat != last_cat else ""
            self._tree.insert("", "end", values=(display_cat, name, value),
                            tags=(tag,))
            last_cat = cat

    # ── Actions ──────────────────────────────────────────────────────

    def _copy_all(self) -> None:
        """Copy all hardware info to clipboard as text."""
        if not self._rows:
            return

        lines: List[str] = []
        last_cat = None
        for cat, name, value in self._rows:
            if cat != last_cat:
                lines.append(f"\n── {cat} ──")
                last_cat = cat
            lines.append(f"  {name}: {value}")

        text = "\n".join(lines).strip()
        self.clipboard_clear()
        self.clipboard_append(text)
        self._set_status("已复制全部硬件信息到剪贴板")
