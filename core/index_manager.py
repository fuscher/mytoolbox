"""core/index_manager.py — JSON索引管理模块。

基于索引的安装包分类方案，支持 CRUD 操作和从物理文件夹结构迁移。
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

from . import get_app_root

INDEX_VERSION = "1.0"


class IndexManager:
    """安装包索引管理器。"""

    def __init__(self, tools_dir: Optional[Path] = None):
        if tools_dir is None:
            tools_dir = get_app_root() / "tools"
        self.tools_dir = tools_dir.resolve()
        self.index_path = self.tools_dir / "_index.json"
        self._load()

    def _load(self) -> None:
        if self.index_path.exists():
            try:
                self.data = json.loads(self.index_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self.data = {"version": INDEX_VERSION, "tools": []}
        else:
            self.data = {"version": INDEX_VERSION, "tools": []}

    def _save(self) -> None:
        self.index_path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    def _generate_id(self, name: str) -> str:
        return re.sub(r'[^a-zA-Z0-9]', '', name.lower().replace(" ", ""))[:20]

    def add_tool(self, tool_info: Dict[str, Any]) -> str:
        tool_id = tool_info.get("id") or self._generate_id(tool_info["name"])
        now = datetime.now().isoformat()
        tool_info["id"] = tool_id
        tool_info["added_at"] = tool_info.get("added_at", now)
        tool_info["updated_at"] = now

        installers = tool_info.get("installers", [])
        total_size = 0
        for installer in installers:
            file_path = self.tools_dir / installer.get("file", "")
            if file_path.exists():
                total_size += file_path.stat().st_size
        tool_info["size"] = total_size

        self.data["tools"].append(tool_info)
        self._save()
        return tool_id

    def update_tool(self, tool_id: str, updates: Dict[str, Any]) -> bool:
        for tool in self.data["tools"]:
            if tool["id"] == tool_id:
                tool.update(updates)
                tool["updated_at"] = datetime.now().isoformat()

                if "installers" in updates:
                    total_size = 0
                    for installer in tool.get("installers", []):
                        file_path = self.tools_dir / installer.get("file", "")
                        if file_path.exists():
                            total_size += file_path.stat().st_size
                    tool["size"] = total_size

                self._save()
                return True
        return False

    def delete_tool(self, tool_id: str) -> bool:
        original_count = len(self.data["tools"])
        self.data["tools"] = [t for t in self.data["tools"] if t["id"] != tool_id]
        if len(self.data["tools"]) < original_count:
            self._save()
            return True
        return False

    def get_tool(self, tool_id: str) -> Optional[Dict[str, Any]]:
        return next((t for t in self.data["tools"] if t["id"] == tool_id), None)

    def get_tools_by_category(self, category_code: str) -> List[Dict[str, Any]]:
        return [t for t in self.data["tools"] if category_code in t.get("categories", [])]

    def search_tools(self, keyword: str) -> List[Dict[str, Any]]:
        keyword = keyword.lower()
        return [t for t in self.data["tools"]
                if keyword in t["name"].lower() or keyword in t.get("description", "").lower()]

    def get_all_tools(self) -> List[Dict[str, Any]]:
        return list(self.data["tools"])

    def batch_update_categories(self, tool_ids: List[str], category_code: str) -> int:
        updated = 0
        for tool in self.data["tools"]:
            if tool["id"] in tool_ids:
                tool["categories"] = [category_code]
                tool["updated_at"] = datetime.now().isoformat()
                updated += 1
        if updated > 0:
            self._save()
        return updated

    def validate_files(self) -> List[str]:
        missing = []
        for tool in self.data["tools"]:
            for installer in tool.get("installers", []):
                file_path = self.tools_dir / installer.get("file", "")
                if not file_path.exists():
                    missing.append(f"{tool['name']}: {installer['file']}")
        return missing

    def validate_and_repair(self) -> List[str]:
        issues = []
        seen_ids = set()
        for tool in self.data["tools"]:
            if tool["id"] in seen_ids:
                issues.append(f"重复 ID: {tool['id']}")
            seen_ids.add(tool["id"])

        missing = self.validate_files()
        issues.extend([f"缺失文件: {m}" for m in missing])

        required_fields = ["id", "name", "categories"]
        for tool in self.data["tools"]:
            for field in required_fields:
                if field not in tool:
                    issues.append(f"缺少字段 {field}: {tool.get('name', 'unknown')}")

        return issues

    def migrate_from_folders(self, categories: List[Dict[str, str]]) -> int:
        migrated = 0
        valid_codes = {cat["code"] for cat in categories}

        for cat in categories:
            code = cat["code"]
            cat_dir = self.tools_dir / code
            if not cat_dir.is_dir():
                continue

            for tool_dir in sorted(cat_dir.iterdir()):
                if not tool_dir.is_dir():
                    continue

                info_path = tool_dir / "info.json"
                if not info_path.exists():
                    continue

                try:
                    raw = json.loads(info_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue

                installers = raw.get("installers", [])
                if not installers:
                    old_installer = raw.get("installer", "")
                    if old_installer:
                        installers = [{"file": f"{code}/{tool_dir.name}/{old_installer}", "label": old_installer}]

                installer_entries = []
                for entry in installers:
                    if isinstance(entry, str):
                        installer_entries.append({
                            "file": f"{code}/{tool_dir.name}/{entry}",
                            "label": entry
                        })
                    elif isinstance(entry, dict):
                        installer_entries.append({
                            "file": f"{code}/{tool_dir.name}/{entry.get('file', '')}",
                            "label": entry.get("label", entry.get("file", ""))
                        })

                tool_categories = [cat for cat in raw.get("categories", [code])
                                  if cat in valid_codes]

                tool_info = {
                    "id": self._generate_id(raw.get("name", tool_dir.name)),
                    "name": raw.get("name", tool_dir.name),
                    "version": raw.get("version"),
                    "description": raw.get("description", ""),
                    "categories": tool_categories,
                    "type": raw.get("type", "exe_installer"),
                    "installers": installer_entries,
                    "folder_path": f"{code}/{tool_dir.name}",
                    "folder_name": tool_dir.name,
                }

                existing = self.get_tool(tool_info["id"])
                if existing:
                    self.update_tool(tool_info["id"], tool_info)
                else:
                    self.add_tool(tool_info)
                migrated += 1

        return migrated

    def get_stats(self) -> Dict[str, Any]:
        tools = self.data["tools"]
        total_size = sum(t.get("size", 0) for t in tools)
        category_counts: Dict[str, int] = {}
        for tool in tools:
            for cat in tool.get("categories", []):
                category_counts[cat] = category_counts.get(cat, 0) + 1

        return {
            "total_tools": len(tools),
            "total_size": total_size,
            "category_distribution": category_counts,
            "index_version": self.data.get("version", INDEX_VERSION),
        }