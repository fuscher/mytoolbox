"""core/app_manager.py — 已安装应用程序管理模块。

参考项目：Bulk Crap Uninstaller (https://github.com/Klocman/Bulk-Crap-Uninstaller)
许可证：Apache License Version 2.0

本模块借鉴了 BCU 的架构设计思想，但代码完全使用 Python 独立实现。
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

try:
    import winreg
    _HAS_WINREG = True
except ImportError:
    _HAS_WINREG = False


class UninstallerType(Enum):
    UNKNOWN = "unknown"
    MSI = "msi"
    NSIS = "nsis"
    INNO_SETUP = "inno"
    STORE_APP = "store"
    EXE = "exe"
    CHOCOLATEY = "chocolatey"


class ConfidenceLevel(Enum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CERTAIN = 4


@dataclass
class InstalledApp:
    name: str
    publisher: Optional[str] = None
    version: Optional[str] = None
    install_date: Optional[str] = None
    install_location: Optional[str] = None
    estimated_size: Optional[int] = None
    uninstall_string: Optional[str] = None
    quiet_uninstall_string: Optional[str] = None
    display_icon: Optional[str] = None
    registry_path: Optional[str] = None
    is_system_component: bool = False
    is_update: bool = False
    is_protected: bool = False
    is_orphaned: bool = False
    is_registered: bool = True
    uninstaller_kind: UninstallerType = UninstallerType.UNKNOWN
    bundle_provider_key: Optional[str] = None
    source: str = "registry"

    def get_size_display(self) -> str:
        if self.estimated_size is None or self.estimated_size <= 0:
            return "-"
        size_kb = self.estimated_size
        if size_kb >= 1024 * 1024:
            return f"{size_kb / (1024 * 1024):.1f} GB"
        elif size_kb >= 1024:
            return f"{size_kb / 1024:.1f} MB"
        else:
            return f"{size_kb} KB"

    def can_uninstall(self) -> bool:
        return bool(self.uninstall_string) and not self.is_system_component

    def detect_uninstaller_type(self) -> UninstallerType:
        if not self.uninstall_string:
            return UninstallerType.UNKNOWN

        cmd = self.uninstall_string.lower()
        if "msiexec" in cmd or "msi" in cmd:
            return UninstallerType.MSI
        if "unins000" in cmd or "unins" in cmd:
            return UninstallerType.NSIS
        if "uninstall.exe" in cmd and ("inno" in self.name.lower() or "setup" in self.name.lower()):
            return UninstallerType.INNO_SETUP
        if "appx" in cmd or "remove-appxpackage" in cmd:
            return UninstallerType.STORE_APP
        if "choco" in cmd or "chocolatey" in cmd:
            return UninstallerType.CHOCOLATEY
        if cmd.endswith(".exe"):
            return UninstallerType.EXE
        return UninstallerType.UNKNOWN


class IUninstallerFactory(ABC):
    @abstractmethod
    def get_entries(self) -> List[InstalledApp]:
        pass

    @property
    @abstractmethod
    def source_name(self) -> str:
        pass


class RegistryFactory(IUninstallerFactory):
    _UNINSTALL_ROOTS = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
         winreg.KEY_WOW64_64KEY if hasattr(winreg, 'KEY_WOW64_64KEY') else 0),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
         winreg.KEY_WOW64_64KEY if hasattr(winreg, 'KEY_WOW64_64KEY') else 0),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", 0),
    ] if _HAS_WINREG else []

    _IGNORED_PUBLISHERS = {"Microsoft Corporation", "Microsoft", "Microsoft Windows", "Mozilla"}

    _SKIP_DIRECTORIES = {
        os.environ.get("WINDIR", "C:\\Windows").lower(),
        os.environ.get("SYSTEMROOT", "C:\\Windows").lower(),
        os.environ.get("PROGRAMDATA", "C:\\ProgramData").lower(),
    }

    @property
    def source_name(self) -> str:
        return "registry"

    def _query_value(self, key, name: str) -> Optional[str]:
        try:
            val, _ = winreg.QueryValueEx(key, name)
            return str(val).strip() if val else None
        except (OSError, FileNotFoundError):
            return None

    def _query_dword(self, key, name: str) -> int:
        try:
            val, _ = winreg.QueryValueEx(key, name)
            return int(val) if val else 0
        except (OSError, FileNotFoundError, ValueError):
            return 0

    def _calculate_directory_size(self, path: str, timeout: int = 3) -> Optional[int]:
        path = path.strip()
        if not path or not os.path.isdir(path):
            return None

        path_lower = path.lower()
        for skip_dir in self._SKIP_DIRECTORIES:
            if path_lower.startswith(skip_dir):
                return None

        try:
            from concurrent.futures import ThreadPoolExecutor, TimeoutError

            def _walk_and_sum():
                total = 0
                for root, _, files in os.walk(path):
                    for f in files:
                        try:
                            total += os.path.getsize(os.path.join(root, f))
                        except OSError:
                            pass
                    if threading.current_thread().ident in getattr(self, '_stopped_threads', set()):
                        break
                return total

            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_walk_and_sum)
                try:
                    size_bytes = future.result(timeout=timeout)
                    return size_bytes // 1024 if size_bytes > 0 else None
                except TimeoutError:
                    return None
        except ImportError:
            try:
                total = 0
                count = 0
                for root, _, files in os.walk(path):
                    for f in files:
                        try:
                            total += os.path.getsize(os.path.join(root, f))
                            count += 1
                            if count > 10000:
                                return None
                        except OSError:
                            pass
                return total // 1024 if total > 0 else None
            except OSError:
                return None

    def _scan_one_root(self, hive, subkey: str, extra_flags: int) -> List[InstalledApp]:
        apps: List[InstalledApp] = []
        if not _HAS_WINREG:
            return apps

        access = winreg.KEY_READ | extra_flags
        try:
            root_key = winreg.OpenKey(hive, subkey, 0, access)
        except OSError:
            return apps

        idx = 0
        while True:
            try:
                key_name = winreg.EnumKey(root_key, idx)
                idx += 1
            except OSError:
                break

            try:
                app_key = winreg.OpenKey(root_key, key_name, 0, access)
            except OSError:
                continue

            try:
                display_name = self._query_value(app_key, "DisplayName")
                if not display_name:
                    winreg.CloseKey(app_key)
                    continue

                if self._query_dword(app_key, "SystemComponent") == 1:
                    winreg.CloseKey(app_key)
                    continue

                publisher = self._query_value(app_key, "Publisher")
                if publisher in self._IGNORED_PUBLISHERS:
                    if "Update" in display_name or "Hotfix" in display_name:
                        winreg.CloseKey(app_key)
                        continue

                version = self._query_value(app_key, "DisplayVersion")
                install_date = self._query_value(app_key, "InstallDate")
                install_location = self._query_value(app_key, "InstallLocation")
                estimated_size = self._query_dword(app_key, "EstimatedSize")
                uninstall_string = self._query_value(app_key, "UninstallString")
                quiet_uninstall = self._query_value(app_key, "QuietUninstallString")
                display_icon = self._query_value(app_key, "DisplayIcon")
                bundle_provider_key = self._query_value(app_key, "BundleProviderKey")

                if install_location:
                    install_location = install_location.strip('"').strip()
                if uninstall_string:
                    uninstall_string = uninstall_string.strip('"').strip()

                if estimated_size <= 0 and install_location and os.path.isdir(install_location):
                    estimated_size = self._calculate_directory_size(install_location)

                app = InstalledApp(
                    name=display_name,
                    publisher=publisher,
                    version=version,
                    install_date=install_date,
                    install_location=install_location,
                    estimated_size=estimated_size if estimated_size and estimated_size > 0 else None,
                    uninstall_string=uninstall_string,
                    quiet_uninstall_string=quiet_uninstall,
                    display_icon=display_icon,
                    registry_path=f"{subkey}\\{key_name}",
                    is_system_component=self._query_dword(app_key, "SystemComponent") == 1,
                    is_update=self._query_dword(app_key, "ParentKeyName") != 0 or "Update" in (display_name or ""),
                    bundle_provider_key=bundle_provider_key,
                    source="registry",
                )
                app.uninstaller_kind = app.detect_uninstaller_type()
                apps.append(app)
            finally:
                winreg.CloseKey(app_key)

        winreg.CloseKey(root_key)
        return apps

    def get_entries(self) -> List[InstalledApp]:
        all_apps: List[InstalledApp] = []
        for hive, subkey, flags in self._UNINSTALL_ROOTS:
            all_apps.extend(self._scan_one_root(hive, subkey, flags))
        return all_apps


class DirectoryFactory(IUninstallerFactory):
    _SEARCH_PATHS = [
        os.environ.get("PROGRAMFILES", "C:\\Program Files"),
        os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)"),
        os.environ.get("LOCALAPPDATA", "C:\\Users\\Default\\AppData\\Local"),
    ]

    @property
    def source_name(self) -> str:
        return "directory"

    def get_entries(self) -> List[InstalledApp]:
        apps: List[InstalledApp] = []
        seen = set()

        for base_path in self._SEARCH_PATHS:
            if not base_path or not os.path.isdir(base_path):
                continue

            try:
                for dir_name in os.listdir(base_path):
                    dir_path = os.path.join(base_path, dir_name)
                    if not os.path.isdir(dir_path):
                        continue

                    if dir_name.lower() in seen:
                        continue
                    seen.add(dir_name.lower())

                    uninstall_exe = os.path.join(dir_path, "uninstall.exe")
                    if not os.path.exists(uninstall_exe):
                        continue

                    size = 0
                    try:
                        for root, _, files in os.walk(dir_path):
                            for f in files:
                                try:
                                    size += os.path.getsize(os.path.join(root, f))
                                except OSError:
                                    pass
                    except OSError:
                        pass

                    app = InstalledApp(
                        name=dir_name,
                        install_location=dir_path,
                        estimated_size=size // 1024 if size > 0 else None,
                        uninstall_string=f'"{uninstall_exe}"',
                        source="directory",
                    )
                    app.uninstaller_kind = app.detect_uninstaller_type()
                    apps.append(app)
            except OSError:
                continue

        return apps


class ApplicationFactoryManager:
    def __init__(self):
        self.factories: List[IUninstallerFactory] = [
            RegistryFactory(),
            DirectoryFactory(),
        ]

    def scan_all(self, callback: Optional[Callable[[int, int, str], None]] = None) -> List[InstalledApp]:
        results: List[InstalledApp] = []
        total = len(self.factories)
        current = 0

        for factory in self.factories:
            current += 1
            if callback:
                callback(current, total, f"正在扫描 {factory.source_name}...")

            entries = factory.get_entries()
            results.extend(entries)

        results = self._deduplicate(results)
        if callback:
            callback(current, total, "扫描完成")

        return results

    def _deduplicate(self, apps: List[InstalledApp]) -> List[InstalledApp]:
        seen: Dict[str, InstalledApp] = {}
        for app in apps:
            name_lower = app.name.lower()
            if name_lower not in seen:
                seen[name_lower] = app
            else:
                if not seen[name_lower].can_uninstall() and app.can_uninstall():
                    seen[name_lower] = app
                elif seen[name_lower].source == "directory" and app.source == "registry":
                    seen[name_lower] = app

        return sorted(seen.values(), key=lambda a: a.name.lower())


def scan_installed_apps() -> List[InstalledApp]:
    manager = ApplicationFactoryManager()
    return manager.scan_all()


def scan_installed_apps_async(callback: Callable[[List[InstalledApp]], None]) -> None:
    def _scan():
        apps = scan_installed_apps()
        callback(apps)

    threading.Thread(target=_scan, daemon=True).start()


def _kill_related_processes(app: InstalledApp) -> int:
    killed = 0
    try:
        import psutil
        for proc in psutil.process_iter(['name', 'exe']):
            try:
                if app.install_location and proc.info['exe']:
                    if proc.info['exe'].lower().startswith(app.install_location.lower()):
                        proc.kill()
                        killed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
    except ImportError:
        pass
    return killed


def uninstall_app(app: InstalledApp, use_quiet: bool = False) -> Tuple[bool, str]:
    if not app.can_uninstall():
        return False, "无法卸载：缺少卸载命令或为系统组件"

    killed = _kill_related_processes(app)
    if killed > 0:
        print(f"已终止 {killed} 个相关进程")

    uninstall_cmd = app.quiet_uninstall_string if use_quiet else app.uninstall_string
    if not uninstall_cmd:
        return False, "无法卸载：缺少卸载命令"

    try:
        if app.uninstaller_kind == UninstallerType.MSI:
            msi_params = uninstall_cmd.replace("MsiExec.exe /I", "/x").replace("MsiExec.exe /X", "/x")
            if use_quiet:
                msi_params += " /qn"
            result = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", "MsiExec.exe", msi_params, None, 1
            )
            if result > 32:
                return True, f"MSI 卸载已启动 ({result})"
            else:
                return False, f"MSI 卸载启动失败 ({result})"
        elif app.uninstaller_kind == UninstallerType.STORE_APP:
            subprocess.Popen(["powershell", f"Remove-AppxPackage {app.name}"])
            return True, "UWP 应用卸载已启动"
        else:
            subprocess.Popen(uninstall_cmd, shell=True)
            return True, "卸载程序已启动"
    except Exception as e:
        return False, f"卸载失败：{str(e)}"


def open_install_location(app: InstalledApp) -> bool:
    if not app.install_location:
        return False

    path = Path(app.install_location)
    if path.exists():
        os.startfile(str(path))
        return True
    return False


def get_app_icon_path(app: InstalledApp) -> Optional[str]:
    if app.display_icon:
        icon_path = app.display_icon.strip('"').split(',')[0]
        if os.path.isfile(icon_path):
            return icon_path

    if app.install_location:
        loc = Path(app.install_location)
        if loc.is_dir():
            for pattern in ["*.ico", "*.exe"]:
                for f in loc.glob(pattern):
                    return str(f)

    return None