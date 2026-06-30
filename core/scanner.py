"""core/scanner.py — Scan the tools/ directory and return structured data.

使用 JSON 索引文件进行分类管理。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional

from .models import CategoryInfo, InstallerEntry, ScanResult, ToolInfo
from .index_manager import IndexManager
from . import get_app_root


def _resolve_tools_dir(config: Optional[dict] = None) -> Path:
    if config and "tools_dir" in config:
        p = Path(config["tools_dir"])
    else:
        p = get_app_root() / "tools"
    return p.resolve()


def _load_categories(tools_dir: Path) -> List[CategoryInfo]:
    cat_file = tools_dir / "_categories.json"
    categories: List[CategoryInfo] = []

    if cat_file.exists():
        raw = json.loads(cat_file.read_text(encoding="utf-8"))
        for code, meta in raw.items():
            categories.append(CategoryInfo(
                code=code,
                display=meta.get("display", code),
                icon=meta.get("icon"),
            ))

    return categories


def _scan_from_index(tools_dir: Path) -> ScanResult:
    manager = IndexManager(tools_dir)
    categories = _load_categories(tools_dir)
    valid_codes = {c["code"] for c in categories}

    tools: Dict[str, ToolInfo] = {}
    all_tools = manager.get_all_tools()

    for tool in all_tools:
        installers = []
        for installer in tool.get("installers", []):
            installers.append(InstallerEntry(
                file=installer.get("file", ""),
                label=installer.get("label", ""),
            ))

        tool_info = ToolInfo(
            id=tool.get("id", ""),
            name=tool.get("name", ""),
            version=tool.get("version"),
            description=tool.get("description", ""),
            installers=installers,
            type=tool.get("type", "exe_installer"),
            categories=tool.get("categories", []),
            folder_path=tool.get("folder_path", ""),
            folder_name=tool.get("folder_name", tool.get("id", "")),
        )

        if not tool_info["categories"]:
            key = f"{tool_info['folder_name']}"
            tools[key] = tool_info
        else:
            for cat_code in tool_info["categories"]:
                if cat_code not in valid_codes:
                    continue
                key = f"{cat_code}/{tool_info['folder_name']}"
                if key not in tools:
                    tools[key] = tool_info

    return ScanResult(categories=categories, tools=tools)


def scan_tools(config: Optional[dict] = None) -> ScanResult:
    tools_dir = _resolve_tools_dir(config)
    return _scan_from_index(tools_dir)


def get_tool_info(folder_path: str, config: Optional[dict] = None) -> Optional[ToolInfo]:
    tools_dir = _resolve_tools_dir(config)
    manager = IndexManager(tools_dir)
    folder_name = folder_path.split("/")[-1] if "/" in folder_path else folder_path
    tool = manager.get_tool(folder_name.lower())
    if not tool:
        return None

    installers = []
    for installer in tool.get("installers", []):
        installers.append(InstallerEntry(
            file=installer.get("file", ""),
            label=installer.get("label", ""),
        ))

    return ToolInfo(
        name=tool.get("name", ""),
        version=tool.get("version"),
        description=tool.get("description", ""),
        installers=installers,
        type=tool.get("type", "exe_installer"),
        categories=tool.get("categories", []),
        folder_path=tool.get("folder_path", folder_path),
        folder_name=folder_name,
    )