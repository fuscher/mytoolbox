"""core/state.py — Read / write installed.json.

All helpers accept an optional *config* dict so the state-file path can be
overridden, but default to ``<project_root>/installed.json``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

from .models import InstalledToolState


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _state_path(config: Optional[dict] = None) -> Path:
    if config and "state_file" in config:
        return Path(config["state_file"]).resolve()
    return Path(__file__).resolve().parent.parent / "installed.json"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_state(config: Optional[dict] = None) -> Dict[str, InstalledToolState]:
    """Return the full installed.json dict (keyed by ``code/folder_name``)."""
    path = _state_path(config)
    if not path.exists():
        return {}
    try:
        raw: dict = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    # Normalise every entry to InstalledToolState shape
    result: Dict[str, InstalledToolState] = {}
    for key, val in raw.items():
        if not isinstance(val, dict):
            continue
        result[key] = InstalledToolState(
            installed=bool(val.get("installed", False)),
            version=val.get("version"),
            uninstall_string=val.get("uninstall_string"),
        )
    return result


def save_state(state: Dict[str, InstalledToolState],
               config: Optional[dict] = None) -> None:
    """Atomically write *state* back to installed.json."""
    path = _state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)                      # atomic on Windows (same volume)


# ── Convenience wrappers ─────────────────────────────────────────────────

def is_installed(key: str, config: Optional[dict] = None) -> bool:
    state = load_state(config)
    return bool(state.get(key, {}).get("installed", False))


def get_tool_state(key: str, config: Optional[dict] = None) -> Optional[InstalledToolState]:
    state = load_state(config)
    return state.get(key)


def mark_installed(key: str, version: Optional[str] = None,
                   config: Optional[dict] = None) -> None:
    state = load_state(config)
    entry = state.get(key, InstalledToolState(installed=False, version=None, uninstall_string=None))
    entry["installed"] = True
    if version:
        entry["version"] = version
    state[key] = entry
    save_state(state, config)


def mark_uninstalled(key: str, config: Optional[dict] = None) -> None:
    state = load_state(config)
    if key in state:
        state[key]["installed"] = False
        save_state(state, config)


def update_uninstall_string(key: str, uninstall_str: str,
                            config: Optional[dict] = None) -> None:
    state = load_state(config)
    if key in state:
        state[key]["uninstall_string"] = uninstall_str
        save_state(state, config)


def get_uninstall_string(key: str, config: Optional[dict] = None) -> Optional[str]:
    state = load_state(config)
    entry = state.get(key)
    if entry:
        return entry.get("uninstall_string")
    return None
