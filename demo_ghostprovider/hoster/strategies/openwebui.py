"""Open WebUI deployment strategy.

Thin wrapper around generic Python hosting that handles:
1. Finding a compatible Python (3.11-3.12) — open-webui requires <3.13
2. Installing backend deps with heavy-package filtering
3. Building the SvelteKit frontend if not pre-built
4. Creating .env with WEBUI_SECRET_KEY

Everything else (venv creation, systemd, startup) delegates to
_host_python_systemd() from the generic Python strategy.
"""

import os
import re
import secrets
import shutil
import subprocess
from pathlib import Path


# Packages too heavy for initial deploy (ML, GPU, cloud SDKs)
_HEAVY_PKGS = frozenset({
    "torch", "torchvision", "torchaudio",
    "triton", "nvidia-cublas", "nvidia-cudnn",
    "nvidia-cufft", "nvidia-cuda-runtime", "nvidia-cuda-nvrtc",
    "nvidia-curand", "nvidia-cusolver", "nvidia-cusparse",
    "nvidia-nvtx", "nvidia-nccl", "nvidia-cusparselt",
})


def _host_openwebui_systemd(project_dir: Path, port: int, repo_url: str = "",
                             sudo_password: bytearray | None = None) -> str:
    """Deploy Open WebUI via generic Python strategy with version constraints.

    Steps:
    1. Find Python 3.11-3.12 (open-webui pyproject.toml: >=3.11, <3.13)
    2. Install backend deps (filtered — skip heavy ML packages)
    3. Build SvelteKit frontend if build/index.html is missing
    4. Create .env with WEBUI_SECRET_KEY
    5. Delegate to _host_python_systemd() with skip_deps=True
    """
    # ══════════════════════════════════════════════
    # Step 1: Find compatible Python (3.11-3.12)
    # ══════════════════════════════════════════════
    compatible_python = _find_compatible_python()
    if not compatible_python:
        raise RuntimeError(
            "Open WebUI requires Python 3.11 or 3.12.\n"
            "Install: sudo pacman -S python311  (or equivalent)"
        )

    # ══════════════════════════════════════════════
    # Step 2: Create venv + install backend deps
    # ══════════════════════════════════════════════
    venv_dir = project_dir / ".venv"
    if not (venv_dir / "bin" / "python").exists():
        subprocess.run(
            [compatible_python, "-m", "venv", str(venv_dir)],
            capture_output=True, timeout=60,
        )
    pip = venv_dir / "bin" / "pip"
    if pip.exists():
        _install_backend_deps(project_dir, pip)

    # ══════════════════════════════════════════════
    # Step 3: Build frontend if needed
    # ══════════════════════════════════════════════
    build_dir = project_dir / "build"
    if not (build_dir / "index.html").exists():
        _build_frontend(project_dir)

    # ══════════════════════════════════════════════
    # Step 4: Create .env
    # ══════════════════════════════════════════════
    _ensure_env(project_dir)

    # ══════════════════════════════════════════════
    # Step 5: Delegate to generic Python strategy
    # ══════════════════════════════════════════════
    from demo_ghostprovider.hoster.strategies.python import _host_python_systemd

    backend_dir = project_dir / "backend"
    return _host_python_systemd(
        project_dir=project_dir,
        port=port,
        repo_url=repo_url or "open-webui",
        python_bin=compatible_python,
        skip_deps=True,
        entry_point="open_webui.main:app",
        extra_env={
            "PYTHONPATH": f"{backend_dir}:{project_dir}",
        },
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


def _install_backend_deps(project_dir: Path, pip: Path) -> None:
    """Install backend dependencies, filtering out heavy ML packages.

    Tries requirements.txt first (from backend/), then falls back to
    installing critical packages directly.
    """
    pip_env = os.environ.copy()
    pip_home = os.path.expanduser("~/localhosts/.tmp")
    os.makedirs(pip_home, exist_ok=True)
    pip_env["TMPDIR"] = pip_home

    # Upgrade pip
    try:
        subprocess.run(
            [str(pip), "install", "--no-cache-dir", "--upgrade", "pip"],
            capture_output=True, timeout=180, env=pip_env,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Try filtered requirements.txt
    backend_dir = project_dir / "backend"
    backend_req = backend_dir / "requirements.txt"
    installed = False

    if backend_req.exists():
        filtered_req = project_dir / ".requirements-light.txt"
        try:
            with open(backend_req) as f_in, open(filtered_req, "w") as f_out:
                for line in f_in:
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#"):
                        f_out.write(line)
                        continue
                    pkg_name = re.split(r"[>=<!\[]", stripped)[0].strip().lower().replace("_", "-")
                    if pkg_name in _HEAVY_PKGS:
                        f_out.write(f"# skipped (heavy): {stripped}\n")
                    else:
                        f_out.write(line)
        except OSError:
            filtered_req = backend_req

        r = subprocess.run(
            [str(pip), "install", "--no-cache-dir", "-r", str(filtered_req)],
            capture_output=True, text=True, timeout=1200, env=pip_env,
        )
        installed = r.returncode == 0
        try:
            filtered_req.unlink(missing_ok=True)
        except OSError:
            pass

    # Always install critical packages — some are undeclared deps
    # (e.g. typer in open_webui.__init__) not in requirements.txt
    critical_pkgs = [
        "uvicorn[standard]", "fastapi", "starlette", "requests",
        "sqlalchemy[asyncio]", "aiosqlite", "alembic",
        "python-multipart", "python-jose[cryptography]",
        "bcrypt", "authlib", "httpx", "pydantic", "pydantic-settings",
        "bs4", "chromadb", "black", "ldap3", "peewee", "mcp",
        "ddgs", "boto3", "azure-identity", "azure-storage-blob",
        "google-cloud-storage", "pycrdt",
        "typer", "click", "rich", "typing-extensions",
        "python-dotenv", "cryptography", "gunicorn",
        "pytest", "pytest-asyncio",
    ]
    subprocess.run(
        [str(pip), "install", "--no-cache-dir"] + critical_pkgs,
        capture_output=True, text=True, timeout=1200, env=pip_env,
    )


def _build_frontend(project_dir: Path) -> None:
    """Build SvelteKit frontend if Node.js 18+ is available.

    Uses progressive fallback: npm install -> with --prefer-offline ->
    with --ignore-scripts. Build timeout is generous (30 min) because
    open-webui has a large dependency tree.
    """
    pkg_json = project_dir / "package.json"
    if not pkg_json.exists():
        return

    node_dir = _find_compatible_node()
    if not node_dir:
        return

    node = os.path.join(node_dir, "node")
    npm = os.path.join(node_dir, "npm")

    npm_env = os.environ.copy()
    npm_env["PATH"] = f"{node_dir}:{npm_env.get('PATH', '')}"
    npm_args = [node, npm]

    # Install deps with progressive fallback
    npm_timeout = 1800  # 30 minutes for heavy projects like open-webui

    # Strategy 1: standard install
    r = subprocess.run(
        npm_args + ["install", "--force", "--no-audit"],
        capture_output=True, text=True, timeout=npm_timeout,
        cwd=str(project_dir), env=npm_env,
    )

    # Strategy 2: retry with --prefer-offline (use cached packages)
    if r.returncode != 0:
        r = subprocess.run(
            npm_args + ["install", "--force", "--no-audit",
                        "--prefer-offline"],
            capture_output=True, text=True, timeout=npm_timeout,
            cwd=str(project_dir), env=npm_env,
        )

    # Strategy 3: skip postinstall scripts (often cause build failures)
    if r.returncode != 0:
        r = subprocess.run(
            npm_args + ["install", "--force", "--ignore-scripts",
                        "--no-audit"],
            capture_output=True, text=True, timeout=npm_timeout,
            cwd=str(project_dir), env=npm_env,
        )

    # Build via npm run build (includes pyodide:fetch step that downloads
    # WASM files needed at runtime).  Direct `vite build` would skip that.
    build_script = None
    try:
        import json as _json
        pkg = _json.loads(pkg_json.read_text())
        if "build" in pkg.get("scripts", {}):
            build_script = "build"
    except (OSError, ValueError):
        pass

    if build_script:
        subprocess.run(
            npm_args + ["run", build_script],
            capture_output=True, text=True, timeout=npm_timeout,
            cwd=str(project_dir), env=npm_env,
        )


def _find_compatible_node() -> str | None:
    """Find Node.js 18+, return directory containing node binary."""
    for candidate in (
        shutil.which("node26"), shutil.which("node24"),
        shutil.which("node22"), shutil.which("node20"), shutil.which("node18"),
        os.path.expanduser("~/.local/share/nodejs/bin/node"),
        shutil.which("node"),
    ):
        if not candidate or not os.path.isfile(candidate):
            continue
        try:
            r = subprocess.run(
                [candidate, "--version"],
                capture_output=True, text=True, timeout=5,
            )
            m = re.search(r"v(\d+)\.", r.stdout)
            if m and int(m.group(1)) >= 18:
                return os.path.dirname(candidate)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue
    return None


def _ensure_env(project_dir: Path) -> None:
    """Create .env with WEBUI_SECRET_KEY if missing."""
    env_file = project_dir / ".env"
    if env_file.exists():
        content = env_file.read_text()
        if "WEBUI_SECRET_KEY" in content:
            return
        content = f"WEBUI_SECRET_KEY={secrets.token_hex(32)}\n{content}"
        env_file.write_text(content)
        env_file.chmod(0o600)
        return

    secret_key = secrets.token_hex(32)
    data_dir = project_dir / "backend" / "data"
    data_dir.mkdir(exist_ok=True)
    env_file.write_text(
        f"WEBUI_SECRET_KEY={secret_key}\n"
        f"OLLAMA_BASE_URL=http://localhost:11434\n"
    )
    env_file.chmod(0o600)
