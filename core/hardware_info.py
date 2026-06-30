"""core/hardware_info.py — 无第三方依赖的硬件信息采集。

全部通过以下方式实现：
  - 通过 ctypes 调用 kernel32（内存信息）
  - 通过 subprocess 调用 wmic（CPU、GPU、磁盘、网络、主板、BIOS）
  - 通过 platform 标准库（操作系统信息）

没有外部依赖。
"""

from __future__ import annotations

import ctypes
import platform
import re
import subprocess
from dataclasses import dataclass, field
from typing import Callable, List, Optional


# ═══════════════════════════════════════════════════════════════════════════
# Data classes
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class CpuInfo:
    name: str = ""
    cores: int = 0
    logical_processors: int = 0
    max_clock_mhz: int = 0
    l2_cache_kb: int = 0
    l3_cache_kb: int = 0


@dataclass
class MemoryStick:
    capacity_gb: float = 0.0
    speed_mhz: int = 0
    part_number: str = ""


@dataclass
class MemoryInfo:
    total_gb: float = 0.0
    available_gb: float = 0.0
    load_percent: int = 0
    sticks: List[MemoryStick] = field(default_factory=list)


@dataclass
class GpuInfo:
    name: str = ""
    vram_mb: int = 0
    driver_version: str = ""


@dataclass
class MotherboardInfo:
    manufacturer: str = ""
    product: str = ""
    bios_vendor: str = ""
    bios_version: str = ""


@dataclass
class DiskInfo:
    model: str = ""
    size_gb: float = 0.0
    media_type: str = ""


@dataclass
class NetworkInfo:
    name: str = ""
    mac: str = ""
    speed_mbps: int = 0


@dataclass
class HardwareProfile:
    cpu: CpuInfo = field(default_factory=CpuInfo)
    memory: MemoryInfo = field(default_factory=MemoryInfo)
    gpus: List[GpuInfo] = field(default_factory=list)
    motherboard: MotherboardInfo = field(default_factory=MotherboardInfo)
    disks: List[DiskInfo] = field(default_factory=list)
    networks: List[NetworkInfo] = field(default_factory=list)
    os_name: str = ""
    os_build: str = ""


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _run_wmic(wmi_class: str, fields: str, where: str = "", use_path: bool = False) -> str:
    """Run a WMIC query and return stdout."""
    args = ["wmic"]
    if where:
        if use_path:
            args.extend(["path", wmi_class, "where", where, "get", fields])
        else:
            args.extend([wmi_class, "where", where, "get", fields])
    elif use_path:
        args.extend(["path", wmi_class, "get", fields])
    else:
        args.extend([wmi_class, "get", fields])
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=10)
        return r.stdout
    except Exception:
        return ""


def _parse_wmic_table(output: str) -> List[dict]:
    """Parse WMIC fixed-width table output into a list of dicts.

    WMIC uses 2+ spaces between columns.  The header line gives column
    order, and we split both header and data on `` {2,}`` boundaries.
    """
    lines = output.splitlines()
    if not lines:
        return []

    # Drop trailing blank lines
    while lines and not lines[-1].strip():
        lines.pop()

    # Find header line
    header_idx = None
    for i, line in enumerate(lines):
        s = line.strip()
        if s and not s.startswith("Node"):
            header_idx = i
            break

    if header_idx is None:
        return []

    header_line = lines[header_idx]
    headers = re.split(r" {2,}", header_line.strip())
    headers = [h.strip() for h in headers if h.strip()]
    if not headers:
        return []

    rows: List[dict] = []
    for line in lines[header_idx + 1:]:
        if not line.strip():
            continue
        values = re.split(r" {2,}", line.strip())
        values = [v.strip() for v in values]
        # Pad short rows with empty strings
        while len(values) < len(headers):
            values.append("")
        # If more values than headers (due to narrow spacing), merge overflow
        if len(values) > len(headers):
            values = values[: len(headers) - 1] + [
                " ".join(values[len(headers) - 1 :])
            ]
        row = dict(zip(headers, values))
        if any(v for v in row.values()):
            rows.append(row)

    return rows


def _bytes_to_gb(b: int) -> float:
    return round(b / (1024 ** 3), 1)


# ═══════════════════════════════════════════════════════════════════════════
# Collectors
# ═══════════════════════════════════════════════════════════════════════════

def _collect_cpu() -> CpuInfo:
    cpu = CpuInfo()
    out = _run_wmic("cpu", "Name,NumberOfCores,NumberOfLogicalProcessors,MaxClockSpeed,L2CacheSize,L3CacheSize")
    rows = _parse_wmic_table(out)
    if not rows:
        return cpu

    r = rows[0]
    cpu.name = r.get("Name", "")
    if cpu.name.startswith("CPU"):
        # Sometimes wmic returns "CPU0" as name — grab full from registry-style
        pass
    cpu.cores = _int_or(r.get("NumberOfCores"), 0)
    cpu.logical_processors = _int_or(r.get("NumberOfLogicalProcessors"), 0)
    cpu.max_clock_mhz = _int_or(r.get("MaxClockSpeed"), 0)
    cpu.l2_cache_kb = _int_or(r.get("L2CacheSize"), 0)
    cpu.l3_cache_kb = _int_or(r.get("L3CacheSize"), 0)
    return cpu


def _collect_memory() -> MemoryInfo:
    mem = MemoryInfo()

    # Total / available via kernel32
    try:
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_uint32),
                ("dwMemoryLoad", ctypes.c_uint32),
                ("ullTotalPhys", ctypes.c_uint64),
                ("ullAvailPhys", ctypes.c_uint64),
                ("ullTotalPageFile", ctypes.c_uint64),
                ("ullAvailPageFile", ctypes.c_uint64),
                ("ullTotalVirtual", ctypes.c_uint64),
                ("ullAvailVirtual", ctypes.c_uint64),
                ("ullAvailExtendedVirtual", ctypes.c_uint64),
            ]

        msx = MEMORYSTATUSEX()
        msx.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(msx))

        mem.total_gb = _bytes_to_gb(msx.ullTotalPhys)
        mem.available_gb = _bytes_to_gb(msx.ullAvailPhys)
        mem.load_percent = msx.dwMemoryLoad
    except Exception:
        pass

    # Per-stick details
    out = _run_wmic("memorychip", "Capacity,Speed,PartNumber,Manufacturer")
    rows = _parse_wmic_table(out)
    for r in rows:
        stick = MemoryStick()
        cap_bytes = _int_or(r.get("Capacity"), 0)
        stick.capacity_gb = _bytes_to_gb(cap_bytes)
        stick.speed_mhz = _int_or(r.get("Speed"), 0)
        stick.part_number = r.get("PartNumber", "").strip()
        if stick.capacity_gb > 0:
            mem.sticks.append(stick)

    return mem


def _collect_gpus() -> List[GpuInfo]:
    gpus = []
    out = _run_wmic("Win32_VideoController",
                    "Name,AdapterRAM,DriverVersion", use_path=True)
    rows = _parse_wmic_table(out)
    for r in rows:
        gpu = GpuInfo()
        gpu.name = r.get("Name", "")
        gpu.driver_version = r.get("DriverVersion", "")
        vram_bytes = _int_or(r.get("AdapterRAM"), 0)
        gpu.vram_mb = round(vram_bytes / (1024 * 1024)) if vram_bytes > 0 else 0
        if gpu.name:
            gpus.append(gpu)
    return gpus


def _collect_bios() -> MotherboardInfo:
    mb = MotherboardInfo()

    # BaseBoard
    out = _run_wmic("baseboard", "Product,Manufacturer")
    rows = _parse_wmic_table(out)
    if rows:
        mb.manufacturer = rows[0].get("Manufacturer", "").strip()
        mb.product = rows[0].get("Product", "").strip()

    # BIOS
    out = _run_wmic("bios", "Manufacturer,SMBIOSBIOSVersion")
    rows = _parse_wmic_table(out)
    if rows:
        mb.bios_vendor = rows[0].get("Manufacturer", "").strip()
        mb.bios_version = rows[0].get("SMBIOSBIOSVersion", "").strip()

    return mb


def _collect_disks() -> List[DiskInfo]:
    disks = []
    out = _run_wmic("diskdrive", "Model,Size,MediaType")
    rows = _parse_wmic_table(out)
    for r in rows:
        d = DiskInfo()
        d.model = r.get("Model", "").strip()
        d.media_type = r.get("MediaType", "").strip()
        size_bytes = _int_or(r.get("Size"), 0)
        d.size_gb = _bytes_to_gb(size_bytes)
        if d.model:
            disks.append(d)
    return disks


def _collect_networks() -> List[NetworkInfo]:
    nets = []
    out = _run_wmic("nic", "Name,Speed,MACAddress", "NetEnabled=true")
    rows = _parse_wmic_table(out)
    for r in rows:
        n = NetworkInfo()
        n.name = r.get("Name", "").strip()
        n.mac = r.get("MACAddress", "").strip()
        n.speed_mbps = _int_or(r.get("Speed"), 0) // 1_000_000  # bps → Mbps
        if n.name:
            nets.append(n)
    return nets


def _int_or(value: str, default: int) -> int:
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


# ═══════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════

def collect_hardware(progress_callback: Optional[Callable[[str], None]] = None) -> HardwareProfile:
    """Collect all hardware information synchronously.

    Args:
        progress_callback: Optional(str) called before each category scan.
    """
    profile = HardwareProfile()

    def step(label: str):
        if progress_callback:
            progress_callback(label)

    step("CPU...")
    profile.cpu = _collect_cpu()

    step("内存...")
    profile.memory = _collect_memory()

    step("GPU...")
    profile.gpus = _collect_gpus()

    step("主板/BIOS...")
    profile.motherboard = _collect_bios()

    step("磁盘...")
    profile.disks = _collect_disks()

    step("网卡...")
    profile.networks = _collect_networks()

    # OS — from stdlib
    step("操作系统...")
    profile.os_name = platform.system() + " " + platform.release()
    profile.os_build = platform.version()

    return profile
