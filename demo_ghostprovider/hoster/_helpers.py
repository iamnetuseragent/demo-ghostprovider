"""Utility helpers for hoster modules."""

import json
import os
import socket
from pathlib import Path


def _read_package_json(project_dir: Path) -> dict | None:
    pkg_file = project_dir / "package.json"
    if not pkg_file.exists():
        return None
    try:
        return json.loads(pkg_file.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _resolve_start_cmd(cmd: str, project_dir: Path) -> str:
    """Resolve relative paths in start commands to absolute paths.

    systemd requires absolute paths in ExecStart. This converts
    commands like './ghost-server --port 8300' to absolute paths.
    """
    if not cmd:
        return cmd
    parts = cmd.split(None, 1)
    if not parts:
        return cmd
    executable = parts[0]
    rest = parts[1] if len(parts) > 1 else ""
    # Only resolve if it's a relative path (not absolute, not a shell builtin)
    if executable.startswith("/") or executable.startswith("/bin/"):
        return cmd
    # Check if the executable exists relative to project_dir
    candidate = project_dir / executable
    if candidate.is_file():
        return f"{candidate} {rest}"
    # Try with ./ prefix stripped
    if executable.startswith("./"):
        candidate = project_dir / executable[2:]
        if candidate.is_file():
            return f"{candidate} {rest}"
    return cmd


def find_free_port(start: int = 0, max_tries: int = 50) -> int:
    """Find the first available port.

    If start is 0, picks a random port in [8000, 30000) to reduce
    collisions with commonly-used ports like 3000 or 8080.
    """
    import random
    if start == 0:
        start = random.randint(8000, 30000)
    for port in range(start, start + max_tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError(f"No free port found in range {start}-{start + max_tries}")
