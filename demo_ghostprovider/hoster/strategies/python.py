"""Python hosting strategy."""

import os
import re
import secrets
import shutil
import subprocess
import uuid
from pathlib import Path

from demo_ghostprovider.hoster.systemd import (
    _create_systemd_service,
    _check_service_started,
    _get_service_logs,
)
from demo_ghostprovider.hoster._helpers import _resolve_start_cmd, _run_build_cmd

# Well-known library directories to skip when searching for user apps
LIBRARY_DIRS = frozenset({
    "flask", "fastapi", "django", "starlette", "tornado", "bottle",
    "aiohttp", "sanic", "falcon", "pyramid", "quart", "cherrypy"
})


def _host_python_systemd(project_dir: Path, port: int, repo_url: str = "",
                         build_cmd: str = "", start_cmd: str = "",
                         python_bin: str = "", skip_deps: bool = False,
                         entry_point: str = "",
                         extra_env: dict[str, str] | None = None) -> str:
    """Host a Python project using systemd.

    Creates an isolated venv, installs deps there, detects entry point,
    and binds to 127.0.0.1 for privacy.

    Falls back to PYTHONPATH-based execution when pip install fails
    (common with projects that have build-time deps like SearXNG).

    If build_cmd/start_cmd are provided from ghostproviderfile, they take
    precedence over auto-detected commands.

    If python_bin is provided (e.g. "/usr/bin/python3.11"), use it for venv
    creation instead of auto-detecting. Used by specialized strategies like
    Open WebUI that require a specific Python version.

    If skip_deps is True, skip pip install (deps already installed by caller).

    If entry_point is provided (e.g. "open_webui.main:app"), use it as the
    ASGI/WSGI module instead of auto-detecting. When the module contains
    ":" it's treated as module:attr; uvicorn is used for ASGI apps.

    If extra_env is provided, merge it into the systemd unit's Environment
    directives (e.g. custom PYTHONPATH for backend/ subdirectory layout).
    """
    service_name = f"ghost-py-{uuid.uuid4().hex[:8]}"

    has_pyproject = (project_dir / "pyproject.toml").exists()
    has_setup = (project_dir / "setup.py").exists()
    has_manage = (project_dir / "manage.py").exists()

    # ── 1. Create venv and install deps ──
    venv_dir = project_dir / ".venv"
    if python_bin:
        python = _ensure_venv(venv_dir, project_dir, python_bin=python_bin)
    else:
        python = _ensure_venv(venv_dir, project_dir)
    python_bin = str(python)

    # Try to install the package — track if it succeeds
    package_installed = False
    if not skip_deps and (has_pyproject or has_setup):
        pip = venv_dir / "bin" / "pip"
        if pip.exists():
            try:
                r = subprocess.run(
                    [str(pip), "install", "-e", "."],
                    capture_output=True, text=True, timeout=1200,
                    cwd=str(project_dir),
                )
                package_installed = r.returncode == 0
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass

    # ── 2. Auto-create .env from .env.example ──
    _auto_env(project_dir)

    # ── 3. Run optional build command from ghostproviderfile ──
    if build_cmd:
        try:
            _run_build_cmd(build_cmd, project_dir, timeout=900)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    # ── 4. Detect entry point and build command ──
    if entry_point:
        # Caller provided explicit entry point (e.g. "open_webui.main:app")
        wsgi_module = entry_point
        is_asgi = True  # Assume ASGI when caller specifies module:attr
    else:
        wsgi_module = _detect_wsgi_module(project_dir)
        is_asgi = False

    py_entry = _detect_python_entry(project_dir)

    # SearXNG / Flask / FastAPI auto-config
    _prepare_searxng_config(project_dir, port)

    # Extra env vars for systemd (PYTHONPATH when package isn't installed)
    env_overrides: dict[str, str] = {}
    python_path_parts = []
    if not package_installed:
        python_path_parts.append(str(project_dir))
    # Add src/ for src-layout projects
    src_dir = project_dir / "src"
    if src_dir.is_dir():
        python_path_parts.append(str(src_dir))
    if python_path_parts:
        env_overrides["PYTHONPATH"] = ":".join(python_path_parts)
    # Merge caller-provided extra_env (overrides auto-detected values)
    if extra_env:
        env_overrides.update(extra_env)

    # Use start_cmd from ghostproviderfile if provided, otherwise auto-detect
    if start_cmd:
        cmd = _resolve_start_cmd(start_cmd, project_dir)
    else:
        # Detect if this is an ASGI app (FastAPI/Starlette) — use uvicorn directly
        if not entry_point and wsgi_module:
            # Auto-detect ASGI from source (only when entry_point not provided)
            module_file = wsgi_module.split(":")[0].replace(".", "/") + ".py"
            src_candidates = [
                project_dir / module_file,
                project_dir / "src" / module_file,
            ]
            for sf in src_candidates:
                if sf.exists():
                    try:
                        content = sf.read_text()
                        if re.search(r"from\s+(fastapi|starlette)\s+import", content):
                            is_asgi = True
                            break
                    except OSError:
                        pass

        if has_manage and wsgi_module:
            if is_asgi:
                cmd = f"{python_bin} -m uvicorn --host 127.0.0.1 --port {port} {wsgi_module}"
            else:
                cmd = (
                    f"/bin/sh -c '{python_bin} -m gunicorn --bind 127.0.0.1:{port} {wsgi_module} "
                    f"|| {python_bin} -m uvicorn --host 127.0.0.1 --port {port} {wsgi_module}'"
                )
        elif has_manage:
            cmd = f"{python_bin} manage.py runserver 127.0.0.1:{port}"
        elif wsgi_module:
            if is_asgi:
                cmd = f"{python_bin} -m uvicorn --host 127.0.0.1 --port {port} {wsgi_module}"
            else:
                cmd = (
                    f"/bin/sh -c '{python_bin} -m gunicorn --bind 127.0.0.1:{port} {wsgi_module} "
                    f"|| {python_bin} -m uvicorn --host 127.0.0.1 --port {port} {wsgi_module}'"
                )
        elif py_entry:
            if "." in py_entry:
                cmd = f"{python_bin} -m {py_entry}"
            else:
                cmd = f"{python_bin} {py_entry}.py"
        else:
            # Last resort: generic HTTP server
            cmd = f"{python_bin} -m http.server {port} --bind 127.0.0.1"

    # ── 5. Create systemd service ──
    _create_systemd_service(
        service_name=service_name,
        working_dir=str(project_dir),
        exec_start=cmd,
        description=f"GhostProvider: {repo_url}",
        port=port,
        extra_env=env_overrides if env_overrides else None,
    )

    # ── 6. Start ──
    try:
        try:
            r = subprocess.run(
                ["systemctl", "--user", "start", service_name],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode != 0:
                raise RuntimeError(f"Failed to start service: {r.stderr}")
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            raise RuntimeError(f"Failed to start service: {e}")

        if not _check_service_started(service_name):
            logs = _get_service_logs(service_name, 15)
            raise RuntimeError(f"Service crashed immediately after start: {logs[:300]}")
    except RuntimeError:
        # Clean up orphan service before re-raising
        from demo_ghostprovider.hoster.systemd import _cleanup_strategy
        from demo_ghostprovider.hoster.analysis import HostResult
        _cleanup_strategy(HostResult(service_names=[service_name]))
        raise

    return service_name


def _ensure_venv(venv_dir: Path, project_dir: Path,
                 python_bin: str = "") -> Path:
    """Create a venv and install project deps into it. Returns path to python.

    Falls back to --system-site-packages when the standard venv lacks
    critical packages (e.g. setuptools on Python 3.14).

    If python_bin is provided, use it instead of "python3" for venv creation.
    """
    py_cmd = python_bin or "python3"

    # Check if existing venv uses the right Python
    venv_python = venv_dir / "bin" / "python"
    venv_ok = False
    if venv_python.exists() and not python_bin:
        try:
            r = subprocess.run(
                [str(venv_python), "--version"],
                capture_output=True, text=True, timeout=5,
            )
            # If venv exists and we're not forcing a specific Python, it's fine
            venv_ok = r.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    if not venv_ok and not venv_dir.exists():
        # Try standard venv first
        try:
            subprocess.run(
                [py_cmd, "-m", "venv", str(venv_dir)],
                capture_output=True, timeout=60,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        # If pip is missing (Python 3.14+), retry with --system-site-packages
        if not (venv_dir / "bin" / "pip").exists():
            shutil.rmtree(venv_dir, ignore_errors=True)
            try:
                subprocess.run(
                    [py_cmd, "-m", "venv", "--system-site-packages", str(venv_dir)],
                    capture_output=True, timeout=60,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass

    python = venv_dir / "bin" / "python"
    pip = venv_dir / "bin" / "pip"

    if pip.exists():
        # Upgrade pip
        try:
            subprocess.run(
                [str(pip), "install", "--upgrade", "pip"],
                capture_output=True, timeout=120,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        # Install requirements.txt
        req = project_dir / "requirements.txt"
        if req.exists():
            try:
                subprocess.run(
                    [str(pip), "install", "-r", str(req)],
                    capture_output=True, text=True, timeout=1200,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass

        # Install WSGI/ASGI servers if not already present
        try:
            subprocess.run(
                [str(pip), "install", "gunicorn", "uvicorn"],
                capture_output=True, text=True, timeout=120,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    if python.exists():
        return python
    return Path("python3")


def _auto_env(project_dir: Path) -> None:
    """Create .env from .env.example if it doesn't exist.

    For SvelteKit projects, ensures all PUB_* variables are defined
    (required at build time by $env/static/public).
    """
    env_file = project_dir / ".env"
    env_example = project_dir / ".env.example"
    if env_file.exists() or not env_example.exists():
        return
    try:
        content = env_example.read_text()

        # SvelteKit: ensure all PUB_* variables are defined
        pub_vars = set()
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                continue
            m = re.match(r"^(?:export\s+)?([A-Z_]+)=.*", stripped)
            if m and m.group(1).startswith("PUB_"):
                pub_vars.add(m.group(1))

        # Add any missing PUB_* vars with empty values
        for var in sorted(pub_vars):
            if var + "=" not in content:
                content += f"\n{var}="

        env_file.write_text(content)
    except OSError:
        pass


def _detect_wsgi_module(project_dir: Path) -> str | None:
    """Try to detect the WSGI/ASGI module from common project structures."""
    manage_py = project_dir / "manage.py"
    if manage_py.exists():
        try:
            for line in manage_py.read_text().splitlines():
                m = re.search(
                    r"setdefault\(\s*['\"]DJANGO_SETTINGS_MODULE['\"]\s*,\s*['\"](.+?)['\"]\s*\)",
                    line,
                )
                if m:
                    settings = m.group(1)
                    return settings.rsplit(".", 1)[0] + ".wsgi:application"
        except OSError:
            pass
        return None

    # Check root-level files first
    for candidate in ("app.py", "main.py"):
        f = project_dir / candidate
        if f.exists():
            try:
                content = f.read_text()
                # Check for actual imports (not just string presence)
                if re.search(r"from\s+(fastapi|starlette)\s+import", content):
                    return f"{candidate[:-3]}:app"
                if re.search(r"from\s+flask\s+import\s+Flask", content):
                    return f"{candidate[:-3]}:app"
            except OSError:
                pass

    # Check src/ layout (src/mypackage/app.py or src/mypackage/main.py)
    # Skip well-known library directories
    src_dir = project_dir / "src"
    if src_dir.is_dir():
        for pkg_dir in src_dir.iterdir():
            if pkg_dir.is_dir() and (pkg_dir / "__init__.py").exists():
                if pkg_dir.name.lower() in LIBRARY_DIRS:
                    continue
                for candidate in ("app.py", "main.py", "wsgi.py", "asgi.py"):
                    f = pkg_dir / candidate
                    if f.exists():
                        try:
                            content = f.read_text()
                            pkg_name = pkg_dir.name
                            module = f"{pkg_name}.{candidate[:-3]}"
                            if re.search(r"from\s+(fastapi|starlette)\s+import", content):
                                return f"{module}:app"
                            if re.search(r"from\s+flask\s+import\s+Flask", content):
                                return f"{module}:app"
                        except OSError:
                            pass

    # Check nested packages (e.g., mypackage/app.py)
    for subdir in project_dir.iterdir():
        if subdir.is_dir() and (subdir / "__init__.py").exists() and subdir.name != "__pycache__":
            for candidate in ("app.py", "main.py", "wsgi.py", "asgi.py"):
                f = subdir / candidate
                if f.exists():
                    try:
                        content = f.read_text()
                        pkg_name = subdir.name
                        module = f"{pkg_name}.{candidate[:-3]}"
                        if re.search(r"from\s+(fastapi|starlette)\s+import", content):
                            return f"{module}:app"
                        if re.search(r"from\s+flask\s+import\s+Flask", content):
                            return f"{module}:app"
                        if "Flask" in content:
                            return f"{module}:app"
                    except OSError:
                        pass

    return None


def _detect_python_entry(project_dir: Path) -> str | None:
    # Check known entry point files
    for entry in ("run.py", "server.py", "webapp.py", "wsgi.py", "asgi.py", "application.py"):
        f = project_dir / entry
        if f.exists():
            return entry[:-3]

    # Check root-level .py files
    for pyfile in project_dir.iterdir():
        if pyfile.suffix == ".py" and pyfile.stem not in ("setup", "conf", "test", "tests", "conftest", "__init__"):
            try:
                content = pyfile.read_text()
                if any(x in content for x in ("app.run", "uvicorn.run", "gunicorn", "web.run", "make_server", "application.run")):
                    return pyfile.stem
            except OSError:
                pass

    # Check src/ layout (skip well-known library directories)
    src_dir = project_dir / "src"
    if src_dir.is_dir():
        for pkg_dir in src_dir.iterdir():
            if pkg_dir.is_dir() and (pkg_dir / "__init__.py").exists():
                if pkg_dir.name.lower() in LIBRARY_DIRS:
                    continue
                for pyfile in pkg_dir.iterdir():
                    if pyfile.suffix == ".py" and pyfile.stem != "__init__":
                        try:
                            content = pyfile.read_text()
                            if any(x in content for x in ("app.run", "uvicorn.run", "gunicorn", "web.run", "make_server", "application.run")):
                                return f"{pkg_dir.name}.{pyfile.stem}"
                        except OSError:
                            pass

    # Check nested packages
    for subdir in project_dir.iterdir():
        if subdir.is_dir() and (subdir / "__init__.py").exists() and subdir.name != "__pycache__":
            for entry in ("webapp", "server", "wsgi", "asgi", "app", "application"):
                candidate = subdir / f"{entry}.py"
                if candidate.exists():
                    return f"{subdir.name}.{entry}"

    return None


def _detect_python_port(project_dir: Path) -> int:
    for yml in ("settings.yml", "settings.yaml", "config.yml", "config.yaml"):
        f = project_dir / yml
        if f.exists():
            try:
                for line in f.read_text().splitlines():
                    m = re.search(r"port\s*[:=]\s*(\d+)", line, re.IGNORECASE)
                    if m:
                        p = int(m.group(1))
                        if 1024 < p < 65536:
                            return p
            except OSError:
                pass
    for pyfile_name in ("settings.py", "config.py", "app.py", "main.py", "webapp.py"):
        pyfile = project_dir / pyfile_name
        if pyfile.exists():
            try:
                for line in pyfile.read_text().splitlines():
                    m = re.search(r"(?:port|PORT)\s*[=:]\s*(\d+)", line)
                    if m:
                        p = int(m.group(1))
                        if 1024 < p < 65536:
                            return p
            except OSError:
                pass
    for subdir in ("src", project_dir.name):
        for pyfile_name in ("settings.py", "config.py", "app.py", "main.py", "webapp.py"):
            pyfile = project_dir / subdir / pyfile_name
            if pyfile.exists():
                try:
                    for line in pyfile.read_text().splitlines():
                        m = re.search(r"(?:port|PORT)\s*[=:]\s*(\d+)", line)
                        if m:
                            p = int(m.group(1))
                            if 1024 < p < 65536:
                                return p
                except OSError:
                    pass
    return 8000


def _prepare_searxng_config(project_dir: Path, port: int = 8888) -> None:
    """Patch SearXNG settings.yml with a real secret_key, bind address, and port."""
    settings_file = project_dir / "searx" / "settings.yml"
    if not settings_file.exists():
        return

    secret_key = secrets.token_hex(32)

    try:
        content = settings_file.read_text()
    except OSError:
        return

    # Replace default secret_key
    content = content.replace('secret_key: "ultrasecretkey"', f'secret_key: "{secret_key}"')

    # Replace port (line starting with whitespace + port: + number)
    content = re.sub(r'^(\s+)port:\s*\d+', rf'\g<1>port: {port}', content, flags=re.MULTILINE)

    # Replace bind_address
    content = re.sub(r'^(\s+)bind_address:\s*"[^"]*"', rf'\g<1>bind_address: "127.0.0.1"', content, flags=re.MULTILINE)

    settings_file.write_text(content)
