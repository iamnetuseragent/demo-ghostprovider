"""Utility helpers for hoster modules."""

import logging
import re
import socket
import subprocess
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger("demo_ghostprovider.hoster._helpers")


_DANGEROUS_CMD_PATTERNS: list[re.Pattern] = [
    re.compile(r'(^|\s)rm\s+(-rf\s+)?/[\w/]*(?:\s|$|&&|\|\|)'),
    re.compile(r'(^|\s)rm\s+(-rf\s+)?/?(?:\$HOME|\$PWD)'),
    re.compile(r'\bmkfs\.'),
    re.compile(r'\bdd\s+if='),
    re.compile(r'\bchmod\s+777\s+/'),
    re.compile(r'\bchown\s+[^/]+\s+/'),
    re.compile(r'\bmv\s+/'),
    re.compile(r'\b>:?\s*/dev/'),
    re.compile(r'\b(wget|curl)\s+\S+\s*\||\|\s*(wget|curl)\b'),
    re.compile(r'\bbash\s+-c\s*["\'].*?\b(rm|mkfs|dd|chmod|chown|wget|curl)\b'),
    re.compile(r'\b(shred|killall|pkill|halt|poweroff|reboot|shutdown)\b'),
]


def _validate_build_cmd(cmd: str) -> None:
    """Validate a build command against dangerous patterns."""
    for pat in _DANGEROUS_CMD_PATTERNS:
        if pat.search(cmd):
            raise RuntimeError(
                f"Build command rejected (matches dangerous pattern '{pat.pattern}'): {cmd[:200]}"
            )


def _run_build_cmd(cmd: str, project_dir: Path, timeout: int = 900,
                   env: dict[str, str] | None = None,
                   on_status: Callable[[str], None] | None = None) -> subprocess.CompletedProcess:
    """Run a build command safely with validation and explicit shell invocation.

    Build steps for the demo services legitimately contain shell syntax
    (``&&``, ``||``, ``cd``, etc.), so they must be run via ``/bin/sh -c``.
    This function:

    1. Validates the command against a blocklist of dangerous patterns
    2. Runs via ``/bin/sh -c`` explicitly (not ``shell=True`` sugar)
    3. Logs the command for audit purposes
    """
    _validate_build_cmd(cmd)
    if on_status:
        on_status(f"build: {cmd[:120]}")
    logger.info("Running build command in %s: %s", project_dir, cmd[:200])
    try:
        result = subprocess.run(
            ["/bin/sh", "-c", cmd],
            capture_output=True, text=True,
            timeout=timeout, cwd=str(project_dir),
            env=env,
            check=False,
        )
        if result.returncode != 0:
            logger.warning("Build command failed (exit %d): %s", result.returncode, result.stderr[:300])
        return result
    except Exception:
        logger.exception("Build command failed: %s", cmd[:200])
        raise


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
