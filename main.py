#!/usr/bin/env python3
"""MyToolbox — 私人工具箱 入口。

Usage
-----
  python main.py                启动 GUI（默认）
  python main.py scan           CLI: 扫描 tools/ 并打印工具列表
  python main.py list           同 scan
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


from core import get_app_root


def _load_config() -> dict:
    cfg_path = get_app_root() / "config.json"
    if cfg_path.exists():
        try:
            return json.loads(cfg_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


# ── CLI handlers ─────────────────────────────────────────────────────────

def cli_scan(config: dict) -> None:
    from core.scanner import scan_tools
    result = scan_tools(config)
    print(f"分类: {len(result['categories'])} 个")
    for cat in result["categories"]:
        print(f"  [{cat['code']}] {cat['display']}")
    print(f"\n工具: {len(result['tools'])} 个")
    for key, tool in sorted(result["tools"].items()):
        ver = tool.get("version") or "-"
        print(f"  {key:30s}  {tool['name']:25s}  v{ver}")


# ── Main ─────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="私人工具箱")
    parser.add_argument("command", nargs="?", default="gui",
                        choices=["gui", "scan", "list"],
                        help="运行模式 (默认 gui)")
    args = parser.parse_args()
    config = _load_config()

    if args.command in ("scan", "list"):
        cli_scan(config)
    else:
        # GUI mode
        try:
            import tkinter
        except ImportError:
            print("错误: 未找到 tkinter，请安装 python3-tk 包。", file=sys.stderr)
            sys.exit(1)

        from gui.app import App
        app = App(config)
        app.mainloop()


if __name__ == "__main__":
    main()
