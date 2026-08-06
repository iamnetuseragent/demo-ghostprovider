"""Persistent deployment state — maps service names to clone paths."""

import fcntl
import json
import os
import logging
from pathlib import Path

logger = logging.getLogger("demo_ghostprovider.state")

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
    """Migrate from unversioned format to version 1."""
    return state


def load() -> dict[str, dict[str, str]]:
    """Load state with file locking."""
    _ensure_state_dir()
    if not STATE_FILE.exists():
        return {}
    fd = -1
    try:
        fd = os.open(str(STATE_FILE), os.O_RDONLY)
        fcntl.flock(fd, fcntl.LOCK_SH)
        size = os.fstat(fd).st_size
        if size == 0:
            return {}
        content = os.read(fd, size).decode("utf-8")
        state = json.loads(content)
        return _migrate(state)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load state: %s", e)
        return {}
    finally:
        if fd >= 0:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
            except OSError:
                pass


def save(state: dict[str, dict[str, str]]) -> None:
    """Save state with file locking."""
    _ensure_state_dir()
    state["version"] = CURRENT_VERSION
    fd = -1
    try:
        fd = os.open(str(STATE_FILE), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        data = json.dumps(state, indent=2).encode("utf-8")
        os.write(fd, data)
    except OSError as e:
        logger.error("Failed to save state: %s", e)
    finally:
        if fd >= 0:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
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
