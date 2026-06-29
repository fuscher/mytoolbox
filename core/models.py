"""MyToolbox data models — TypedDict definitions for all core data structures."""

from __future__ import annotations
from typing import TypedDict, Optional, List, Dict


# ── Category ─────────────────────────────────────────────────────────────

class CategoryInfo(TypedDict):
    """A single category entry from _categories.json."""
    code: str        # e.g. "001"
    display: str     # e.g. "编辑器"
    icon: Optional[str]


# ── Installer entry (one version of an installer file) ───────────────────

class InstallerEntry(TypedDict):
    """One installer file inside a tool's installers array."""
    file: str        # filename, e.g. "VSCodeSetup-x64-1.90.0.exe"
    label: str       # display label, e.g. "v1.90.0 x64"


# ── Tool info (from info.json) ───────────────────────────────────────────

class ToolInfo(TypedDict):
    """Parsed info.json for a single tool."""
    id: str                          # unique identifier
    name: str                        # display name
    version: Optional[str]           # version string (may be None)
    description: Optional[str]       # short description
    installers: List[InstallerEntry] # list of installer files
    type: str                        # "exe_installer" | "msi_installer" | "archive"
    categories: List[str]            # category codes, e.g. ["001", "002"]
    folder_path: str                 # relative path from tools/, e.g. "001/VSCode"
    folder_name: str                 # folder name, e.g. "VSCode"


# ── Installed state (from installed.json) ────────────────────────────────

class InstalledToolState(TypedDict, total=False):
    """State of an installed tool in installed.json."""
    installed: bool
    version: Optional[str]
    uninstall_string: Optional[str]


# ── Aggregate results ────────────────────────────────────────────────────

class ScanResult(TypedDict):
    """Full scan result from core/scanner.py."""
    categories: List[CategoryInfo]               # ordered list
    tools: Dict[str, ToolInfo]                   # key = "code/folder_name"
