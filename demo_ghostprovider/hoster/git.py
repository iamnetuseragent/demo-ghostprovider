"""Git clone operations with retry and tarball fallback."""

import os
import shutil
import subprocess
import tempfile
import time
from typing import Callable


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
    """
    def _emit(msg: str) -> None:
        if on_status:
            on_status(msg)

    if os.path.isdir(os.path.join(dest, ".git")):
        return True
    if os.path.isdir(dest):
        shutil.rmtree(dest, ignore_errors=True)

    env = _git_env()
    timeout = 600

    clone_strategies = [
        ["git", "clone", "--depth", "1", "--single-branch", "--no-tags", url, dest],
        ["git", "clone", "--single-branch", url, dest],
    ]

    for attempt in range(retries):
        _emit(f"cloning (attempt {attempt + 1}/{retries})...")

        cmd = clone_strategies[attempt % len(clone_strategies)]

        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=timeout, env=env,
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

    for tb_url in tarball_urls:
        try:
            _emit("downloading tarball...")
            # Use secure temp file
            fd, tmp_path = tempfile.mkstemp(suffix=".tar.gz", prefix="gp-tarball-")
            os.close(fd)
            proc = subprocess.run(
                ["curl", "-4", "-sL", "-o", tmp_path, tb_url],
                capture_output=True, text=True, timeout=300,
            )
            if proc.returncode == 0 and os.path.getsize(tmp_path) > 1000:
                _emit("extracting tarball...")
                os.makedirs(dest, exist_ok=True)
                proc2 = subprocess.run(
                    ["tar", "xzf", tmp_path, "-C", dest, "--strip-components=1"],
                    capture_output=True, text=True, timeout=120,
                )
                os.remove(tmp_path)
                if proc2.returncode == 0:
                    subprocess.run(["git", "init"], capture_output=True, cwd=dest, timeout=10)
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
