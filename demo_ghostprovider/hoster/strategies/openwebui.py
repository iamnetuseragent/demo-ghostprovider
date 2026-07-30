"""Open WebUI deployment strategy.

Installs ``open-webui`` from PyPI — no npm, no requirements.txt, no
manual dependency resolution. Everything comes pre-bundled in the wheel.

Steps:
1. Find a compatible Python (3.11-3.12) — open-webui requires <3.13
2. Create venv, pip install open-webui
3. Pass WEBUI_SECRET_KEY and DATA_DIR via systemd Environment
4. Delegate to _host_python_systemd() with skip_deps=True
"""

import os
import re
import secrets
import shutil
import subprocess
from pathlib import Path


def _host_openwebui_systemd(project_dir: Path, port: int, repo_url: str = "",
                             sudo_password: bytearray | None = None) -> str:
    compatible_python = _find_compatible_python()
    if not compatible_python:
        raise RuntimeError(
            "Open WebUI requires Python 3.11 or 3.12.\n"
            "Install: sudo pacman -S python311  (or equivalent)"
        )

    venv_dir = project_dir / ".venv"
    if not (venv_dir / "bin" / "python").exists():
        subprocess.run(
            [compatible_python, "-m", "venv", str(venv_dir)],
            capture_output=True, timeout=60,
        )

    pip = venv_dir / "bin" / "pip"
    if pip.exists():
        try:
            subprocess.run(
                [str(pip), "install", "--no-cache-dir", "--upgrade", "pip"],
                capture_output=True, timeout=180,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        pip_tmp = project_dir / ".pip-tmp"
        pip_tmp.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["TMPDIR"] = str(pip_tmp)

        try:
            r = subprocess.run(
                [str(pip), "install", "--no-cache-dir", "open-webui"],
                capture_output=True, text=True, timeout=1200,
                env=env,
            )
            if r.returncode != 0:
                msg = r.stderr.strip() or r.stdout.strip() or "unknown error"
                raise RuntimeError(
                    f"pip install open-webui failed:\n{msg[:500]}"
                )
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                "pip install open-webui timed out after 1200s.\n"
                "Check your internet connection and try again."
            )
        finally:
            shutil.rmtree(pip_tmp, ignore_errors=True)

    data_dir = project_dir / "data"
    data_dir.mkdir(exist_ok=True)
    secret_key = secrets.token_hex(32)

    extra_env = {
        "FROM_INIT_PY": "true",
        "WEBUI_SECRET_KEY": secret_key,
        "DATA_DIR": str(data_dir),
    }

    from demo_ghostprovider.hoster.strategies.python import _host_python_systemd

    return _host_python_systemd(
        project_dir=project_dir,
        port=port,
        repo_url=repo_url or "open-webui",
        python_bin=compatible_python,
        skip_deps=True,
        entry_point="open_webui.main:app",
        extra_env=extra_env,
    )


def _find_compatible_python() -> str | None:
    """Find Python 3.11 or 3.12 on the system."""
    for py_name in ("python3.11", "python3.12"):
        py_path = shutil.which(py_name)
        if py_path:
            try:
                r = subprocess.run(
                    [py_path, "--version"],
                    capture_output=True, text=True, timeout=5,
                )
                m = re.search(r"(\d+)\.(\d+)", r.stdout)
                if m and int(m.group(2)) in (11, 12):
                    return py_path
            except (subprocess.TimeoutExpired, FileNotFoundError):
                continue
    return None


