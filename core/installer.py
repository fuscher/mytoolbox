"""core/installer.py — Launch an installer and detect success via registry polling.

All heavy work (Popen + registry polling) runs in a **background daemon
thread** so the tkinter mainloop is never blocked.

Public API
----------
install_tool(tool_info, installer_index, config, on_status)
    Start the install flow.  Returns immediately; results arrive via *on_status*.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional, Set

from .models import ToolInfo
from . import state as state_mod


# Optional winreg import (only works on Windows)
try:
    import winreg
    _HAS_WINREG = True
except ImportError:
    _HAS_WINREG = False


# ---------------------------------------------------------------------------
# Registry helpers (Windows only)
# ---------------------------------------------------------------------------

_UNINSTALL_KEYS = [
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_CURRENT_USER,  r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
] if _HAS_WINREG else []


def _snapshot_display_names() -> Set[str]:
    """Return a set of all DisplayName values currently in the Uninstall keys."""
    names: Set[str] = []
    names_set: Set[str] = set()
    if not _HAS_WINREG:
        return names_set

    for hive, subkey in _UNINSTALL_KEYS:
        try:
            key = winreg.OpenKey(hive, subkey, 0,
                                 winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0))
        except OSError:
            continue
        idx = 0
        while True:
            try:
                sub = winreg.EnumKey(key, idx)
                idx += 1
            except OSError:
                break
            try:
                sk = winreg.OpenKey(key, sub)
                name_val, _ = winreg.QueryValueEx(sk, "DisplayName")
                sys_comp = 0
                try:
                    sys_comp, _ = winreg.QueryValueEx(sk, "SystemComponent")
                except OSError:
                    pass
                winreg.CloseKey(sk)
                if name_val and not sys_comp:
                    names_set.add(name_val.strip())
            except OSError:
                continue
        winreg.CloseKey(key)

    return names_set


def _snapshot_display_names_with_uninstall() -> dict:
    """Return {DisplayName: UninstallString} for newly installed detection."""
    result: dict = {}
    if not _HAS_WINREG:
        return result

    for hive, subkey in _UNINSTALL_KEYS:
        try:
            key = winreg.OpenKey(hive, subkey, 0,
                                 winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0))
        except OSError:
            continue
        idx = 0
        while True:
            try:
                sub = winreg.EnumKey(key, idx)
                idx += 1
            except OSError:
                break
            try:
                sk = winreg.OpenKey(key, sub)
                name_val, _ = winreg.QueryValueEx(sk, "DisplayName")
                sys_comp = 0
                try:
                    sys_comp, _ = winreg.QueryValueEx(sk, "SystemComponent")
                except OSError:
                    pass
                uninst = ""
                try:
                    uninst, _ = winreg.QueryValueEx(sk, "UninstallString")
                except OSError:
                    pass
                winreg.CloseKey(sk)
                if name_val and not sys_comp:
                    result[name_val.strip()] = uninst
            except OSError:
                continue
        winreg.CloseKey(key)

    return result


# ---------------------------------------------------------------------------
# Core install flow (runs in background thread)
# ---------------------------------------------------------------------------

StatusCallback = Callable[[str, str], None]
# callback(status, detail)
#   status ∈ {"launching", "polling", "installed", "timeout", "error", "cancelled"}


def _resolve_installer_path(tool_info: ToolInfo, installer_index: int,
                            config: Optional[dict]) -> Path:
    """Build absolute path to the chosen installer file."""
    from . import get_app_root
    tools_dir = Path(config.get("tools_dir", "")).resolve() if config else \
                get_app_root() / "tools"
    installers = tool_info.get("installers", [])
    if not installers:
        raise FileNotFoundError(f"工具 {tool_info['name']} 没有安装包")
    idx = min(installer_index, len(installers) - 1)
    return tools_dir / tool_info["folder_path"] / installers[idx]["file"]


def _run_install(tool_info: ToolInfo, installer_index: int,
                 config: Optional[dict], on_status: StatusCallback) -> None:
    """Thread target — the full install + registry-poll lifecycle."""
    key = tool_info["folder_path"]
    tool_name = tool_info["name"]
    timeout_min = (config or {}).get("install_timeout_minutes", 10)
    poll_interval = (config or {}).get("registry_poll_interval_seconds", 2)

    # ── 1. Resolve installer path ───────────────────────────────────────
    try:
        installer_path = _resolve_installer_path(tool_info, installer_index, config)
    except FileNotFoundError as exc:
        on_status("error", str(exc))
        return

    if not installer_path.exists():
        on_status("error", f"安装包不存在: {installer_path}")
        return

    # ── 2. Snapshot registry BEFORE launching ───────────────────────────
    before_names = _snapshot_display_names()
    before_full = _snapshot_display_names_with_uninstall()

    # ── 3. Launch installer or open archive ─────────────────────────────
    on_status("launching", str(installer_path))
    try:
        ext = installer_path.suffix.lower()
        archive_extensions = {".zip", ".7z", ".rar"}
        
        if ext in archive_extensions:
            result = ctypes.windll.shell32.ShellExecuteW(
                None, "open", str(installer_path), None, None, 1
            )
            if result > 32:
                on_status("installed", f"已用默认程序打开压缩包")
            else:
                on_status("error", f"无法打开压缩包")
            return
        elif ext == ".msi":
            result = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", "msiexec", f"/i \"{installer_path}\"", None, 1
            )
        else:
            result = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", str(installer_path), None, None, 1
            )
        if result <= 32:
            on_status("error", f"无法启动安装程序: UAC 提升失败或被拒绝")
            return
    except Exception as exc:
        on_status("error", f"无法启动安装程序: {exc}")
        return

    # ── 4. Poll registry until new entry appears or timeout ─────────────
    on_status("polling", "等待安装完成...")
    deadline = time.time() + timeout_min * 60
    found_name: Optional[str] = None
    found_uninst: Optional[str] = None

    while time.time() < deadline:
        time.sleep(poll_interval)
        after_full = _snapshot_display_names_with_uninstall()
        new_names = set(after_full.keys()) - set(before_full.keys())

        # Filter out Windows components that may have appeared
        new_names = {n for n in new_names
                     if not n.startswith("Microsoft") and "KB" not in n.upper()
                     and "Update" not in n}

        if new_names:
            # Pick the entry whose name is closest to tool_name
            best = min(new_names, key=lambda n: _levenshtein(n.lower(), tool_name.lower()))
            found_name = best
            found_uninst = after_full.get(best, "")
            break

    # ── 5. Report result ────────────────────────────────────────────────
    if found_name:
        version = tool_info.get("version")
        state_mod.mark_installed(key, version)
        if found_uninst:
            state_mod.update_uninstall_string(key, found_uninst)
        on_status("installed", found_name)
    else:
        on_status("timeout", f"安装超时（{timeout_min} 分钟），未检测到新程序")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def install_tool(tool_info: ToolInfo,
                 installer_index: int = 0,
                 config: Optional[dict] = None,
                 on_status: Optional[StatusCallback] = None) -> None:
    """Start the visual install flow in a background thread.

    Parameters
    ----------
    tool_info : ToolInfo
        The tool to install (from scanner).
    installer_index : int
        Which installer to run if the tool has multiple versions.
    config : dict, optional
        App config (for tools_dir, timeout, poll_interval).
    on_status : callable, optional
        ``(status: str, detail: str) -> None`` — called from the background
        thread to report progress.  **UI updates must be scheduled via
        ``root.after()``** from within the callback.
    """
    if on_status is None:
        on_status = lambda s, d: None

    thread = threading.Thread(
        target=_run_install,
        args=(tool_info, installer_index, config, on_status),
        daemon=True,
    )
    thread.start()


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _levenshtein(s: str, t: str) -> int:
    """Simple Levenshtein distance for name matching."""
    if s == t:
        return 0
    if not s:
        return len(t)
    if not t:
        return len(s)
    prev = list(range(len(t) + 1))
    for i, sc in enumerate(s, 1):
        curr = [i] + [0] * len(t)
        for j, tc in enumerate(t, 1):
            cost = 0 if sc == tc else 1
            curr[j] = min(curr[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
        prev = curr
    return prev[len(t)]
