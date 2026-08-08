"""Utility helpers for hoster modules."""

import logging
import os
import re
import shutil
import socket
import subprocess
import uuid
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


# ── Build sandbox ────────────────────────────────────────────────────────
#
# Build/install steps run inside a systemd-run --user transient unit with
# hardening properties (filesystem read-only outside the project, no new
# privileges, no devices, empty capability set). Tool caches are redirected
# under <project>/.ghost-cache and wiped after each run, so builds never
# write to the user's shared caches. This limits damage from malicious or
# broken build scripts but runs as the same user, so it is NOT a trust
# boundary — only a blast-radius reducer. See README threat model.


def _sandbox_enabled() -> bool:
    """Whether build/install steps run inside the systemd-run sandbox.

    Opt out per invocation with GHOSTPROVIDER_NO_SANDBOX=1 (some builds need
    unusual system access and break inside the sandbox).
    """
    return os.environ.get("GHOSTPROVIDER_NO_SANDBOX", "") not in ("1", "true", "yes", "on")


_SANDBOX_PROPERTIES: tuple[str, ...] = (
    "NoNewPrivileges=yes",
    "PrivateTmp=yes",
    "ProtectSystem=strict",
    "ProtectHome=read-only",
    "PrivateDevices=yes",
    "ProtectControlGroups=yes",
    "ProtectKernelTunables=yes",
    "ProtectKernelModules=yes",
    "RestrictNamespaces=yes",
    "LockPersonality=yes",
    "RestrictRealtime=yes",
    "RestrictSUIDSGID=yes",
    "CapabilityBoundingSet=",
)


def _cache_env(project_dir: str | None) -> dict[str, str]:
    """Redirect every tool cache under <project_dir>/.ghost-cache/....

    Builds never write to the user's shared caches (~/.cache, ~/.npm,
    ~/.cargo, ...); instead each cache var points inside the project. The
    directory is wiped after every sandboxed command (see _run_sandboxed),
    so nothing survives a build. Returns an empty dict when no project
    directory is known.
    """
    if not project_dir:
        return {}
    base = os.path.join(project_dir, ".ghost-cache")
    return {
        "XDG_CACHE_HOME": os.path.join(base, "xdg"),
        "npm_config_cache": os.path.join(base, "npm"),
        "YARN_CACHE_FOLDER": os.path.join(base, "yarn"),
        "BUN_INSTALL_CACHE_DIR": os.path.join(base, "bun"),
        "CARGO_HOME": os.path.join(base, "cargo"),
        "GOCACHE": os.path.join(base, "go"),
        "GOMODCACHE": os.path.join(base, "go-mod"),
        "GOPATH": os.path.join(base, "go-path"),
        "GOTMPDIR": os.path.join(base, "go-tmp"),
        "npm_config_store_dir": os.path.join(base, "pnpm"),
        "PNPM_HOME": os.path.join(base, "pnpm-home"),
        "TMPDIR": os.path.join(base, "tmp"),
    }


def _strip_systemd_status(err: str) -> str:
    """Remove systemd-run's status preamble/summary from captured stderr."""
    err = re.sub(r'^Running as unit: .*\n', '', err, count=1, flags=re.MULTILINE)
    err = re.sub(r'\n\s*Finished with result:.*$', '', err, flags=re.DOTALL)
    return err


def _run_plain(cmd: list[str], cwd: str | None = None,
               env: dict[str, str] | None = None,
               timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True,
        timeout=timeout, cwd=cwd, env=env, check=False,
    )


def _run_sandboxed(cmd: list[str], cwd: str | None = None,
                   env: dict[str, str] | None = None,
                   timeout: int = 900,
                   readwrite: list[str] | None = None) -> subprocess.CompletedProcess:
    """Run a command inside a hardened systemd-run --user transient unit.

    Falls back to a plain subprocess when systemd-run is missing or the user
    systemd manager is unavailable, so deployments never break on machines
    without it. The sandbox is still same-user; it limits damage (filesystem,
    devices, privileges), not user-level trust.
    """
    # Cache redirection always applies (sandboxed or not); it overrides any
    # caller-provided cache vars and the .ghost-cache dir is wiped afterwards.
    cache_env = _cache_env(cwd)
    run_env = env if env is not None else os.environ.copy()
    run_env.update(cache_env)

    try:
        if not _sandbox_enabled() or shutil.which("systemd-run") is None:
            return _run_plain(cmd, cwd=cwd, env=run_env, timeout=timeout)

        unit = f"ghost-build-{uuid.uuid4().hex[:8]}.service"
        args = ["systemd-run", "--user", "--wait", "--pipe", "--collect", "--unit", unit]
        if cwd:
            args.append(f"--working-directory={cwd}")
        else:
            args.append("--same-dir")

        rw = list(readwrite) if readwrite else []
        for prop in _SANDBOX_PROPERTIES:
            args.append(f"--property={prop}")
        for path in rw:
            args.append(f"--property=ReadWritePaths={path}")

        for key, value in run_env.items():
            args.append(f"--setenv={key}={value}")
        args.append("--")
        args.extend(cmd)

        try:
            proc = subprocess.run(
                args, capture_output=True, text=True,
                timeout=timeout, env=run_env, check=False,
            )
        except subprocess.TimeoutExpired:
            subprocess.run(
                ["systemctl", "--user", "kill", unit],
                capture_output=True, timeout=10, check=False,
            )
            raise
        except (FileNotFoundError, OSError):
            return _run_plain(cmd, cwd=cwd, env=run_env, timeout=timeout)

        if proc.returncode != 0 and "running as unit" not in proc.stderr.lower():
            # The unit never started (no user manager / DBus) — run directly.
            logger.warning(
                "systemd-run sandbox unavailable (%s); falling back to direct execution",
                proc.stderr.strip().splitlines()[0] if proc.stderr.strip() else proc.returncode,
            )
            return _run_plain(cmd, cwd=cwd, env=run_env, timeout=timeout)

        proc.stderr = _strip_systemd_status(proc.stderr)
        return proc
    finally:
        if cache_env:
            shutil.rmtree(os.path.join(str(cwd), ".ghost-cache"), ignore_errors=True)


def _run_build_cmd(cmd: str, project_dir: Path, timeout: int = 900,
                   env: dict[str, str] | None = None,
                   on_status: Callable[[str], None] | None = None) -> subprocess.CompletedProcess:
    """Run a build command safely with validation and explicit shell invocation.

    Build steps for the demo services legitimately contain shell syntax
    (``&&``, ``||``, ``cd``, etc.), so they must be run via ``/bin/sh -c``.
    This function:

    1. Validates the command against a blocklist of dangerous patterns
    2. Runs via ``/bin/sh -c`` inside the systemd-run sandbox
    3. Logs the command for audit purposes
    """
    _validate_build_cmd(cmd)
    if on_status:
        on_status(f"build: {cmd[:120]}")
    logger.info("Running build command in %s: %s", project_dir, cmd[:200])
    try:
        result = _run_sandboxed(
            ["/bin/sh", "-c", cmd],
            cwd=str(project_dir),
            env=env,
            timeout=timeout,
            readwrite=[str(project_dir)],
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
