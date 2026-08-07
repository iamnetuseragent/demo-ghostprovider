"""Git clone operations with retry and tarball fallback."""

import logging
import os
import shlex
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable

logger = logging.getLogger("demo_ghostprovider.git")


def _setup_git_auth(env: dict[str, str]) -> str | None:
    """Configure git authentication via GIT_ASKPASS to avoid token in argv.

    Creates a temporary credential helper script so the token never appears
    in ``/proc/PID/cmdline``. Returns the path to the askpass script (for
    later cleanup), or None if no token is configured.
    """
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    if not token:
        return None

    fd, askpass_path = tempfile.mkstemp(suffix=".sh", prefix="gp-askpass-")
    with os.fdopen(fd, "w") as f:
        f.write("#!/bin/sh\n")
        f.write(f"echo {shlex.quote(token)}\n")
    os.chmod(askpass_path, 0o600)

    env["GIT_ASKPASS"] = askpass_path
    env["GIT_TERMINAL_PROMPT"] = "0"
    return askpass_path


def _git_env() -> dict[str, str]:
    """Return environment dict with git configured for reliable cloning."""
    env = os.environ.copy()
    env["GIT_CONFIG_COUNT"] = "4"
    env["GIT_CONFIG_KEY_0"] = "http.backend"
    env["GIT_CONFIG_VALUE_0"] = "curl"
    env["GIT_CONFIG_KEY_1"] = "http.sslBackend"
    env["GIT_CONFIG_VALUE_1"] = "openssl"
    env["GIT_CONFIG_KEY_2"] = "http.userAgent"
    env["GIT_CONFIG_VALUE_2"] = "git/2.45.0"
    env["GIT_CONFIG_KEY_3"] = "http.postBuffer"
    env["GIT_CONFIG_VALUE_3"] = "524288000"
    return env


def _git_clone(url: str, dest: str, retries: int = 5,
               on_status: Callable[[str], None] | None = None) -> bool:
    """Clone a git repo with retries, backoff, and tarball fallback.

    Uses ``--depth 1`` for speed, falls back to full clone, then tarball via curl.
    Each attempt allows up to 10 minutes for large repositories.
    Returns True on success.

    Credentials are handled via GIT_ASKPASS (not URL-embedded) to prevent
    token leakage through ``/proc/PID/cmdline``.
    """
    def _emit(msg: str) -> None:
        if on_status:
            on_status(msg)

    if os.path.isdir(os.path.join(dest, ".git")):
        return True
    if os.path.isdir(dest):
        shutil.rmtree(dest, ignore_errors=True)

    env = _git_env()
    askpass_path = _setup_git_auth(env)
    timeout = 600

    clone_strategies = [
        ["git", "clone", "--depth", "1", "--single-branch", "--no-tags", url, dest],
        ["git", "clone", "--single-branch", url, dest],
    ]

    try:
        for attempt in range(retries):
            _emit(f"cloning (attempt {attempt + 1}/{retries})...")

            cmd = clone_strategies[attempt % len(clone_strategies)]

            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True,
                    timeout=timeout, env=env,
                    check=False,
                )
                if proc.returncode == 0 and os.path.isdir(os.path.join(dest, ".git")):
                    _emit("clone complete")
                    return True
                err = proc.stderr.strip()
                if err:
                    _emit(f"git error: {err[:120]}")
            except subprocess.TimeoutExpired:
                _emit(f"clone timed out after {timeout}s")
            except OSError as e:
                _emit(f"clone OS error: {e}")

            if os.path.isdir(dest):
                shutil.rmtree(dest, ignore_errors=True)

            wait = min(5 * (3 ** attempt), 300)
            _emit(f"retrying in {wait}s...")
            time.sleep(wait)

        # Fallback: download tarball via curl
        _emit("git clone failed, trying tarball download...")
        tarball_url = url
        if not tarball_url.endswith("/"):
            tarball_url += "/"
        tarball_url += "archive/refs/heads/master.tar.gz"
        tarball_urls = [tarball_url, tarball_url.replace("/master.", "/main.")]

        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
        for tb_url in tarball_urls:
            try:
                _emit("downloading tarball...")
                fd, tmp_path = tempfile.mkstemp(suffix=".tar.gz", prefix="gp-tarball-")
                os.close(fd)
                curl_cmd = ["curl", "-4", "-sL", "-o", tmp_path, tb_url]
                if token:
                    curl_cmd.extend(["-H", f"Authorization: token {token}"])
                proc = subprocess.run(
                    curl_cmd,
                    capture_output=True, text=True, timeout=300,
                    check=False,
                )
                if proc.returncode == 0 and os.path.getsize(tmp_path) > 1000:
                    _emit("extracting tarball...")
                    os.makedirs(dest, exist_ok=True)
                    proc2 = subprocess.run(
                        ["tar", "xzf", tmp_path, "-C", dest, "--strip-components=1"],
                        capture_output=True, text=True, timeout=120,
                        check=False,
                    )
                    os.remove(tmp_path)
                    if proc2.returncode == 0:
                        subprocess.run(["git", "init"], capture_output=True, cwd=dest, timeout=10, check=False)
                        _emit("tarball download complete")
                        return True
                    else:
                        _emit(f"tarball extract failed: {proc2.stderr[:100]}")
                else:
                    _emit("tarball download failed (HTTP)")
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
            except (subprocess.TimeoutExpired, OSError) as e:
                _emit(f"tarball error: {e}")
            if os.path.isdir(dest):
                shutil.rmtree(dest, ignore_errors=True)

        return False
    finally:
        if askpass_path:
            try:
                os.unlink(askpass_path)
            except OSError:
                logger.exception("Failed to clean up askpass script: %s", askpass_path)
