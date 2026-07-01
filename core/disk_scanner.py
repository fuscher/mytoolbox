"""core/disk_scanner.py — High-performance disk space scanner.

Scans a directory tree using ``os.scandir()`` (the fastest stdlib option —
wraps Windows ``FindFirstFileW`` / ``FindNextFileW`` directly).  Designed to
be called from a background thread; progress is reported via a callback.

Usage::

    from core.disk_scanner import scan_directory, ScanResult, format_size

    result = scan_directory("C:\\Users", on_progress=lambda done, total: ...)
    print(f"Total: {format_size(result.total_size)} in {result.total_files} files, "
          f"{result.total_dirs} folders")
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional


# ═══════════════════════════════════════════════════════════════════════════
# Data types
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class FileEntry:
    """One file or folder within the scanned tree."""
    name: str
    path: str        # absolute path
    size: int        # bytes
    is_dir: bool
    modified: str    # ISO-formatted datetime string (or empty on error)
    extension: str   # lowercase file extension (empty for dirs / no-ext files)


@dataclass
class ScanResult:
    """Complete result of a directory scan."""
    root_path: str
    total_size: int
    total_files: int
    total_dirs: int
    # Mapping: parent_absolute_path → list of child FileEntry (direct children only)
    children: Dict[str, List[FileEntry]] = field(default_factory=dict)

    @property
    def root_entries(self) -> List[FileEntry]:
        """Direct children of the scanned root directory."""
        return self.children.get(self.root_path, [])


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def format_size(size_bytes: int) -> str:
    """Convert a byte count into a human-readable string (KB / MB / GB).

    >>> format_size(0)
    '0 B'
    >>> format_size(512)
    '512 B'
    >>> format_size(1536)
    '1.50 KB'
    >>> format_size(2_500_000)
    '2.38 MB'
    """
    if size_bytes < 1024:
        return f"{size_bytes} B"
    for unit in ("KB", "MB", "GB", "TB"):
        size_bytes /= 1024.0
        if size_bytes < 1024:
            return f"{size_bytes:.2f} {unit}"
    return f"{size_bytes:.2f} PB"


def _guess_extension(name: str, is_dir: bool) -> str:
    """Return a lowercase human-readable 'type' string for a file/dir."""
    if is_dir:
        return "文件夹"
    _, dot, ext = name.rpartition(".")
    if dot:
        return f"{ext.upper()} 文件"
    return "文件"


def _safe_modified(path: str) -> str:
    """Return ISO-format mtime or empty string on error."""
    try:
        ts = os.path.getmtime(path)
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except OSError:
        return ""


# ═══════════════════════════════════════════════════════════════════════════
# Scanner
# ═══════════════════════════════════════════════════════════════════════════

def _count_items(root: str) -> int:
    """Quickly count total files + dirs for progress estimation."""
    count = 0
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            count += len(dirnames) + len(filenames)
    except OSError:
        pass
    return max(count, 1)  # avoid division by zero


def scan_directory(
    root_path: str,
    on_progress: Optional[Callable[[int, int], None]] = None,
    stop_event: Optional[threading.Event] = None,
    report_interval: int = 500,
) -> ScanResult:
    """Scan *root_path* recursively and return a ``ScanResult``.

    Parameters
    ----------
    root_path:
        Absolute or relative path to scan.
    on_progress:
        Called periodically with ``(done, total)`` where *total* is an
        estimate from a quick ``os.walk`` pass and *done* is items processed.
    stop_event:
        When set, the scan stops early and returns whatever was collected.
    report_interval:
        Call *on_progress* every N items (default 500).

    Returns
    -------
    ScanResult
        Flat structured result — the ``children`` dict maps every scanned
        directory path to its direct ``FileEntry`` children.
    """
    root_path = os.path.abspath(root_path)
    stop_event = stop_event or threading.Event()

    # ── Pre-count for progress bar ────────────────────────────────────
    total_estimate = _count_items(root_path)

    children: Dict[str, List[FileEntry]] = {}
    total_size = 0
    total_files = 0
    total_dirs = 0
    processed = 0

    def _collect(dirpath: str):
        """Recursively collect entries from *dirpath* into *children*."""
        nonlocal total_size, total_files, total_dirs, processed

        entries: List[FileEntry] = []
        subdirs: List[str] = []

        try:
            with os.scandir(dirpath) as it:
                for entry in it:
                    if stop_event.is_set():
                        return

                    processed += 1
                    if processed % report_interval == 0 and on_progress:
                        on_progress(processed, total_estimate)

                    try:
                        is_dir = entry.is_dir(follow_symlinks=False)
                    except OSError:
                        # Permission error or broken symlink — treat as file
                        is_dir = False

                    try:
                        st = entry.stat(follow_symlinks=False)
                        size = st.st_size if not is_dir else 0
                    except OSError:
                        size = 0

                    fe = FileEntry(
                        name=entry.name,
                        path=entry.path,
                        size=size,
                        is_dir=is_dir,
                        modified=_safe_modified(entry.path),
                        extension=_guess_extension(entry.name, is_dir),
                    )
                    entries.append(fe)

                    if is_dir:
                        total_dirs += 1
                        subdirs.append(entry.path)
                    else:
                        total_files += 1
                        total_size += size

        except PermissionError:
            pass  # skip directories we can't read
        except OSError:
            pass

        # Sort: dirs first, then files, alphabetical within each group
        entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
        children[dirpath] = entries

        # Recurse into subdirectories
        for sub in subdirs:
            if stop_event.is_set():
                return
            _collect(sub)

    # ── Run ───────────────────────────────────────────────────────────
    _collect(root_path)

    # Final progress
    if on_progress:
        on_progress(processed, total_estimate)

    return ScanResult(
        root_path=root_path,
        total_size=total_size,
        total_files=total_files,
        total_dirs=total_dirs,
        children=children,
    )
