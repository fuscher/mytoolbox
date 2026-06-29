"""core/junk_scanner.py — 应用程序残留清理模块。

参考项目：Bulk Crap Uninstaller (https://github.com/Klocman/Bulk-Crap-Uninstaller)
许可证：Apache License Version 2.0

本模块借鉴了 BCU 的残留清理架构设计，但代码完全使用 Python 独立实现，
并采用自主设计的 4 级置信度评分体系。
"""

from __future__ import annotations

import os
import shutil
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import winreg
    _HAS_WINREG = True
except ImportError:
    _HAS_WINREG = False


class ConfidenceLevel(Enum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CERTAIN = 4

    def get_color(self) -> str:
        colors = {
            ConfidenceLevel.LOW: "#FF6B6B",
            ConfidenceLevel.MEDIUM: "#FFD93D",
            ConfidenceLevel.HIGH: "#6BCB77",
            ConfidenceLevel.CERTAIN: "#4D96FF",
        }
        return colors[self]

    def get_description(self) -> str:
        descriptions = {
            ConfidenceLevel.LOW: "低置信度，需谨慎",
            ConfidenceLevel.MEDIUM: "中等置信度，建议确认",
            ConfidenceLevel.HIGH: "高置信度，可安全删除",
            ConfidenceLevel.CERTAIN: "确定为残留，可直接删除",
        }
        return descriptions[self]


class JunkType(Enum):
    FILE = "file"
    DIRECTORY = "directory"
    REGISTRY_KEY = "registry_key"
    REGISTRY_VALUE = "registry_value"
    SHORTCUT = "shortcut"
    STARTUP = "startup"


@dataclass
class JunkResult:
    path: str
    type: JunkType
    confidence: ConfidenceLevel
    description: str
    size: Optional[int] = None

    def get_size_display(self) -> str:
        if self.size is None or self.size <= 0:
            return "-"
        if self.size >= 1024 * 1024:
            return f"{self.size / (1024 * 1024):.1f} GB"
        elif self.size >= 1024:
            return f"{self.size / 1024:.1f} MB"
        else:
            return f"{self.size} KB"


class IJunkScanner(ABC):
    @abstractmethod
    def scan(self, app_name: str, app_location: Optional[str],
             registry_path: Optional[str]) -> List[JunkResult]:
        pass

    @property
    @abstractmethod
    def category_name(self) -> str:
        pass

    @property
    def timeout(self) -> float:
        return 10.0


class InstallLocationScanner(IJunkScanner):
    @property
    def category_name(self) -> str:
        return "安装目录"

    @property
    def timeout(self) -> float:
        return 5.0

    def scan(self, app_name: str, app_location: Optional[str],
             registry_path: Optional[str]) -> List[JunkResult]:
        results: List[JunkResult] = []
        if not app_location or not os.path.exists(app_location):
            return results

        try:
            total_size = 0
            file_count = 0
            for root, dirs, files in os.walk(app_location):
                for f in files:
                    try:
                        total_size += os.path.getsize(os.path.join(root, f))
                        file_count += 1
                        if file_count > 5000:
                            break
                    except OSError:
                        pass
                if file_count > 5000:
                    break

            results.append(JunkResult(
                path=app_location,
                type=JunkType.DIRECTORY,
                confidence=ConfidenceLevel.HIGH,
                description=f"应用安装目录: {app_name}",
                size=total_size,
            ))
        except OSError:
            pass

        return results


class RegistryJunkScanner(IJunkScanner):
    _REGISTRY_PATHS = [
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node"),
    ] if _HAS_WINREG else []

    @property
    def category_name(self) -> str:
        return "注册表残留"

    @property
    def timeout(self) -> float:
        return 8.0

    def _scan_registry_tree(self, key, app_name_lower: str,
                           base_path: str, depth: int = 0) -> List[JunkResult]:
        results: List[JunkResult] = []
        if depth > 2:
            return results

        try:
            idx = 0
            max_keys = 50
            while idx < max_keys:
                try:
                    subkey_name = winreg.EnumKey(key, idx)
                    idx += 1
                except OSError:
                    break

                if app_name_lower in subkey_name.lower():
                    full_path = f"{base_path}\\{subkey_name}"
                    results.append(JunkResult(
                        path=full_path,
                        type=JunkType.REGISTRY_KEY,
                        confidence=ConfidenceLevel.MEDIUM,
                        description=f"注册表键: {subkey_name}",
                    ))

                try:
                    subkey = winreg.OpenKey(key, subkey_name)
                    results.extend(self._scan_registry_tree(subkey, app_name_lower,
                                                             f"{base_path}\\{subkey_name}", depth + 1))
                    winreg.CloseKey(subkey)
                except OSError:
                    continue
        except OSError:
            pass
        return results

    def scan(self, app_name: str, app_location: Optional[str],
             registry_path: Optional[str]) -> List[JunkResult]:
        results: List[JunkResult] = []
        if not _HAS_WINREG or not app_name:
            return results

        app_name_lower = app_name.lower()

        for hive, base_path in self._REGISTRY_PATHS:
            try:
                key = winreg.OpenKey(hive, base_path, 0, winreg.KEY_READ)
                results.extend(self._scan_registry_tree(key, app_name_lower,
                                                         f"{'HKCU' if hive == winreg.HKEY_CURRENT_USER else 'HKLM'}\\{base_path}"))
                winreg.CloseKey(key)
            except OSError:
                continue

        if registry_path:
            try:
                hive = winreg.HKEY_LOCAL_MACHINE if "HKLM" in registry_path.upper() else winreg.HKEY_CURRENT_USER
                path = registry_path.replace("HKLM\\", "").replace("HKCU\\", "")
                key = winreg.OpenKey(hive, path, 0, winreg.KEY_READ)
                results.append(JunkResult(
                    path=registry_path,
                    type=JunkType.REGISTRY_KEY,
                    confidence=ConfidenceLevel.CERTAIN,
                    description=f"卸载注册表键",
                ))
                winreg.CloseKey(key)
            except OSError:
                pass

        return results


class ShortcutScanner(IJunkScanner):
    _SHORTCUT_PATHS = [
        os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu"),
        os.path.join(os.environ.get("PROGRAMDATA", ""), "Microsoft", "Windows", "Start Menu"),
        os.path.join(os.environ.get("PUBLIC", ""), "Desktop"),
        os.path.join(os.environ.get("USERPROFILE", ""), "Desktop"),
    ]

    @property
    def category_name(self) -> str:
        return "快捷方式"

    @property
    def timeout(self) -> float:
        return 3.0

    def scan(self, app_name: str, app_location: Optional[str],
             registry_path: Optional[str]) -> List[JunkResult]:
        results: List[JunkResult] = []
        if not app_name:
            return results

        app_name_lower = app_name.lower()

        for base_path in self._SHORTCUT_PATHS:
            if not base_path or not os.path.isdir(base_path):
                continue

            try:
                for root, dirs, files in os.walk(base_path):
                    for f in files:
                        if f.lower().endswith(".lnk"):
                            if app_name_lower in f.lower():
                                full_path = os.path.join(root, f)
                                try:
                                    size = os.path.getsize(full_path)
                                except OSError:
                                    size = None
                                results.append(JunkResult(
                                    path=full_path,
                                    type=JunkType.SHORTCUT,
                                    confidence=ConfidenceLevel.HIGH,
                                    description=f"快捷方式: {f}",
                                    size=size,
                                ))
            except OSError:
                continue

        return results


class StartupScanner(IJunkScanner):
    _STARTUP_REGISTRY_PATHS = [
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce"),
    ] if _HAS_WINREG else []

    @property
    def category_name(self) -> str:
        return "启动项"

    @property
    def timeout(self) -> float:
        return 3.0

    def scan(self, app_name: str, app_location: Optional[str],
             registry_path: Optional[str]) -> List[JunkResult]:
        results: List[JunkResult] = []
        if not _HAS_WINREG or not app_name:
            return results

        app_name_lower = app_name.lower()

        for hive, base_path in self._STARTUP_REGISTRY_PATHS:
            try:
                key = winreg.OpenKey(hive, base_path, 0, winreg.KEY_READ)
                idx = 0
                while True:
                    try:
                        value_name = winreg.EnumValue(key, idx)
                        idx += 1
                    except OSError:
                        break

                    name = value_name[0]
                    data = str(value_name[1]) if value_name[1] else ""

                    if app_name_lower in name.lower() or app_name_lower in data.lower():
                        full_path = f"{'HKCU' if hive == winreg.HKEY_CURRENT_USER else 'HKLM'}\\{base_path}\\{name}"
                        results.append(JunkResult(
                            path=full_path,
                            type=JunkType.STARTUP,
                            confidence=ConfidenceLevel.HIGH,
                            description=f"启动项: {name}",
                        ))

                winreg.CloseKey(key)
            except OSError:
                continue

        return results


class PrefetchScanner(IJunkScanner):
    _PREFETCH_PATH = os.path.join(os.environ.get("WINDIR", ""), "Prefetch")

    @property
    def category_name(self) -> str:
        return "Prefetch"

    @property
    def timeout(self) -> float:
        return 2.0

    def scan(self, app_name: str, app_location: Optional[str],
             registry_path: Optional[str]) -> List[JunkResult]:
        results: List[JunkResult] = []
        if not app_name or not self._PREFETCH_PATH or not os.path.isdir(self._PREFETCH_PATH):
            return results

        app_name_lower = app_name.lower()

        try:
            for f in os.listdir(self._PREFETCH_PATH):
                if f.lower().endswith(".pf"):
                    if app_name_lower in f.lower():
                        full_path = os.path.join(self._PREFETCH_PATH, f)
                        try:
                            size = os.path.getsize(full_path)
                        except OSError:
                            size = None
                        results.append(JunkResult(
                            path=full_path,
                            type=JunkType.FILE,
                            confidence=ConfidenceLevel.CERTAIN,
                            description=f"Prefetch 文件: {f}",
                            size=size,
                        ))
        except OSError:
            pass

        return results


class JunkManager:
    def __init__(self):
        self.scanners: List[IJunkScanner] = [
            InstallLocationScanner(),
            RegistryJunkScanner(),
            ShortcutScanner(),
            StartupScanner(),
            PrefetchScanner(),
        ]
        self._stop_event = threading.Event()

    def scan_junk(self, app_name: str, app_location: Optional[str] = None,
                  registry_path: Optional[str] = None,
                  callback: Optional[Callable[[int, int, str], None]] = None) -> List[JunkResult]:
        self._stop_event.clear()
        all_junk: List[JunkResult] = []
        total = len(self.scanners)
        completed = 0

        try:
            from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError

            with ThreadPoolExecutor(max_workers=3) as executor:
                futures = {}
                for scanner in self.scanners:
                    future = executor.submit(self._scan_with_timeout, scanner, app_name,
                                            app_location, registry_path)
                    futures[future] = scanner

                for future in as_completed(futures, timeout=30):
                    scanner = futures[future]
                    completed += 1
                    try:
                        junk = future.result()
                        all_junk.extend(junk)
                        if callback:
                            callback(completed, total, f"完成 {scanner.category_name} 扫描")
                    except TimeoutError:
                        if callback:
                            callback(completed, total, f"{scanner.category_name} 扫描超时")
                    except Exception:
                        if callback:
                            callback(completed, total, f"{scanner.category_name} 扫描出错")

                    if self._stop_event.is_set():
                        break

        except ImportError:
            for scanner in self.scanners:
                if self._stop_event.is_set():
                    break
                completed += 1
                try:
                    junk = self._scan_with_timeout(scanner, app_name, app_location, registry_path)
                    all_junk.extend(junk)
                except Exception:
                    pass
                if callback:
                    callback(completed, total, f"完成 {scanner.category_name}")

        return self._cleanup_results(all_junk)

    def _scan_with_timeout(self, scanner: IJunkScanner, app_name: str,
                           app_location: Optional[str],
                           registry_path: Optional[str]) -> List[JunkResult]:
        result = []
        exception = None

        def target():
            nonlocal result, exception
            try:
                result = scanner.scan(app_name, app_location, registry_path)
            except Exception as e:
                exception = e

        thread = threading.Thread(target=target)
        thread.start()
        thread.join(timeout=scanner.timeout)

        if thread.is_alive():
            raise TimeoutError(f"Scanner {scanner.category_name} timed out")
        if exception:
            raise exception

        return result

    def stop_scan(self) -> None:
        self._stop_event.set()

    def _cleanup_results(self, junk: List[JunkResult]) -> List[JunkResult]:
        seen = set()
        cleaned = []
        for item in junk:
            key = f"{item.type.value}:{item.path.lower()}"
            if key not in seen:
                seen.add(key)
                cleaned.append(item)
        return cleaned

    def delete_junk(self, junk: List[JunkResult],
                    min_confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM) -> Tuple[int, int]:
        deleted = 0
        skipped = 0

        for item in junk:
            if item.confidence.value < min_confidence.value:
                skipped += 1
                continue

            try:
                if item.type in (JunkType.FILE, JunkType.SHORTCUT):
                    if os.path.isfile(item.path):
                        os.remove(item.path)
                        deleted += 1
                elif item.type == JunkType.DIRECTORY:
                    if os.path.isdir(item.path):
                        shutil.rmtree(item.path)
                        deleted += 1
                elif item.type in (JunkType.REGISTRY_KEY, JunkType.STARTUP):
                    if _HAS_WINREG:
                        parts = item.path.split("\\")
                        hive_str = parts[0]
                        path = "\\".join(parts[1:])

                        hive = {
                            "HKCU": winreg.HKEY_CURRENT_USER,
                            "HKLM": winreg.HKEY_LOCAL_MACHINE,
                        }.get(hive_str)

                        if hive:
                            try:
                                winreg.DeleteKey(hive, path)
                                deleted += 1
                            except OSError:
                                pass
            except Exception:
                pass

        return deleted, skipped

    def get_stats(self, junk: List[JunkResult]) -> Dict[str, Any]:
        by_type: Dict[str, int] = {}
        by_confidence: Dict[str, int] = {}
        total_size = 0

        for item in junk:
            by_type[item.type.value] = by_type.get(item.type.value, 0) + 1
            by_confidence[item.confidence.name] = by_confidence.get(item.confidence.name, 0) + 1
            if item.size:
                total_size += item.size

        return {
            "total_items": len(junk),
            "total_size": total_size,
            "by_type": by_type,
            "by_confidence": by_confidence,
        }