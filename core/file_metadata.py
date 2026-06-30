"""core/file_metadata.py — 从 Windows 安装包文件中提取详细元数据。

支持从 .exe 文件（PE 格式）读取 VS_VERSIONINFO 资源信息，
以及从 .msi 文件读取 Property 表数据。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
import struct
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── Optional imports ────────────────────────────────────────────────────────

try:
    import msilib
    _HAS_MSILIB = True
except ImportError:
    _HAS_MSILIB = False


# ═══════════════════════════════════════════════════════════════════════════════
# Data model
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class FileMetadata:
    """Extracted installer file metadata."""

    file_path: str = ""
    file_type: str = ""       # "Windows 可执行文件 (.exe)" etc.
    file_size: int = 0
    file_size_str: str = ""   # Human-readable ("6.69 MB")
    modified_date: str = ""   # "2024-12-15 10:30:00"

    # PE version info (may be empty for files without VS_VERSIONINFO)
    file_description: str = ""
    file_version: str = ""
    product_name: str = ""
    product_version: str = ""
    company_name: str = ""
    copyright: str = ""


# ═══════════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════════

def get_file_metadata(file_path: str | Path) -> Optional[FileMetadata]:
    """Extract metadata from a Windows installer file (.exe / .msi).

    Returns ``None`` if the file doesn't exist or isn't a supported type.
    """
    path = Path(file_path)
    if not path.is_file():
        return None

    ext = path.suffix.lower()

    # Always-populated fields (filesystem)
    result = FileMetadata(
        file_path=str(path),
        file_type=_format_file_type(ext),
        file_size=path.stat().st_size,
        file_size_str=_format_size(path.stat().st_size),
        modified_date=datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
    )

    if ext == ".exe":
        _fill_exe_metadata(path, result)
    elif ext == ".msi":
        _fill_msi_metadata(path, result)

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# .exe / PE  metadata via version.dll
# ═══════════════════════════════════════════════════════════════════════════════

def _fill_exe_metadata(path: Path, result: FileMetadata) -> None:
    """Fill *result* with VS_VERSIONINFO strings from a PE file.

    Uses the Win32 version.dll API (GetFileVersionInfoSizeW,
    GetFileVersionInfoW, VerQueryValueW) via ctypes.  All strings are
    UTF-16 in the PE resource; the API returns them as Python str
    (ctypes converts WCHAR*).
    """
    try:
        version_dll = ctypes.windll.version
        kernel32 = ctypes.windll.kernel32
    except Exception:
        return

    file_path_wstr = str(path)

    # ── Allocate buffer ──────────────────────────────────────────────────
    size = version_dll.GetFileVersionInfoSizeW(file_path_wstr, None)
    if size == 0:
        return

    buf = (ctypes.c_byte * size)()
    if not version_dll.GetFileVersionInfoW(file_path_wstr, 0, size, buf):
        return

    # ── Language / codepage (first translation pair) ─────────────────────
    lang_cp = _ver_query_translation(version_dll, buf, size)
    if not lang_cp:
        return
    lang_hex, cp_hex = lang_cp

    # ── String block ─────────────────────────────────────────────────────
    base = f"\\StringFileInfo\\{lang_hex:04X}{cp_hex:04X}\\"

    strings_to_extract = [
        ("file_description", "FileDescription"),
        ("file_version",     "FileVersion"),
        ("product_name",     "ProductName"),
        ("product_version",  "ProductVersion"),
        ("company_name",     "CompanyName"),
        ("copyright",        "LegalCopyright"),
    ]

    for field, key in strings_to_extract:
        sub_block = base + key
        value = _ver_query_string(version_dll, buf, sub_block)
        if value:
            # Strip any trailing null chars left by ctypes conversion
            value = value.rstrip("\x00").strip()
            if value:
                setattr(result, field, value)


def _ver_query_translation(version_dll, buf, size: int) -> Optional[tuple[int, int]]:
    """Read the first (language, codepage) pair from \\VarFileInfo\\Translation."""
    ptr = ctypes.c_void_p()
    length = ctypes.c_uint(0)

    ok = version_dll.VerQueryValueW(buf, "\\VarFileInfo\\Translation",
                                    ctypes.byref(ptr), ctypes.byref(length))
    if not ok or length.value < 4:
        return None

    # Each translation entry is 4 bytes: 2-byte language + 2-byte codepage
    raw = ctypes.string_at(ptr, length.value)
    lang = struct.unpack_from("<H", raw, 0)[0]
    cp = struct.unpack_from("<H", raw, 2)[0]
    return lang, cp


def _ver_query_string(version_dll, buf, sub_block: str) -> Optional[str]:
    """Query a single version-info string; returns ``None`` on failure."""
    ptr = ctypes.c_void_p()
    length = ctypes.c_uint(0)

    ok = version_dll.VerQueryValueW(buf, sub_block,
                                    ctypes.byref(ptr), ctypes.byref(length))
    if not ok or length.value == 0 or ptr.value is None:
        return None

    # The buffer is a null-terminated wide string; ctypes.wstring_at
    # handles length in characters (not bytes).
    try:
        return ctypes.wstring_at(ptr, length.value)
    except Exception:
        # Very rare edge-case: if the string is malformed, fall back to
        # reading without explicit length.
        try:
            return ctypes.wstring_at(ptr)
        except Exception:
            return None


# ═══════════════════════════════════════════════════════════════════════════════
# .msi metadata via msilib
# ═══════════════════════════════════════════════════════════════════════════════

def _fill_msi_metadata(path: Path, result: FileMetadata) -> None:
    """Fill *result* with Property-table data from an MSI database."""
    if not _HAS_MSILIB:
        return

    try:
        db = msilib.OpenDatabase(str(path), msilib.MSIDBOPEN_READONLY)
    except Exception:
        return

    try:
        # MSI SQL doesn't support IN (...), so we query all Property rows
        # and filter in Python.
        view = db.OpenView("SELECT Property, Value FROM Property")
        view.Execute(None)

        record = view.Fetch()
        while record:
            prop = record.GetString(1)
            value = record.GetString(2)

            if prop == "ProductName":
                result.product_name = value
            elif prop == "ProductVersion":
                result.product_version = value
            elif prop == "Manufacturer":
                result.company_name = value

            record = view.Fetch()

        view.Close()
        db.Close()

    except Exception:
        try:
            db.Close()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _format_file_type(ext: str) -> str:
    """Return a human-readable file-type label for an extension."""
    mapping = {
        ".exe": "Windows 可执行文件 (.exe)",
        ".msi": "Windows 安装程序包 (.msi)",
        ".msu": "Windows 更新包 (.msu)",
        ".zip": "ZIP 压缩文件 (.zip)",
        ".7z":  "7-Zip 压缩文件 (.7z)",
        ".rar": "RAR 压缩文件 (.rar)",
    }
    return mapping.get(ext, f"文件 ({ext})")


def _format_size(size_bytes: int) -> str:
    """Format a byte count into a human-friendly string ("6.69 MB")."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"
