"""Persistent deployment state — maps service names to clone paths."""

import json
import os
from pathlib import Path

STATE_DIR = Path.home() / ".config" / "demo-ghostprovider"
STATE_FILE = STATE_DIR / "state.json"
CURRENT_VERSION = 1


def _ensure_state_dir() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def _migrate(state: dict) -> dict:
    """Migrate state to the current format version."""
    v = state.get("version", 0)
    if v < 1:
        state = _migrate_v0_to_v1(state)
    state["version"] = CURRENT_VERSION
    return state


def _migrate_v0_to_v1(state: dict) -> dict:
    """Migrate from unversioned format to version 1.

    v0 was a flat dict of {service_name: {clone_path, repo_url}}.
    v1 adds a top-level "version" key. No data changes needed.
    """
    return state


def load() -> dict[str, dict[str, str]]:
    _ensure_state_dir()
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text())
            return _migrate(state)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save(state: dict[str, dict[str, str]]) -> None:
    _ensure_state_dir()
    state["version"] = CURRENT_VERSION
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2))
    except OSError:
        pass


def register(service_name: str, clone_path: str, repo_url: str) -> None:
    state = load()
    state[service_name] = {"clone_path": clone_path, "repo_url": repo_url}
    save(state)


def unregister(service_name: str) -> None:
    state = load()
    state.pop(service_name, None)
    save(state)


def get_clone_path(service_name: str) -> str | None:
    state = load()
    entry = state.get(service_name)
    if isinstance(entry, dict) and os.path.isdir(entry.get("clone_path", "")):
        return entry["clone_path"]
    return None


def find_by_repo_url(repo_url: str) -> str | None:
    state = load()
    for key, entry in state.items():
        if key == "version":
            continue
        if isinstance(entry, dict) and entry.get("repo_url") == repo_url:
            if os.path.isdir(entry.get("clone_path", "")):
                return entry["clone_path"]
    return None
