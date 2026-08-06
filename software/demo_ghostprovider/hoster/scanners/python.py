"""Python dependency and source-code analysis."""

import re
from pathlib import Path
from typing import Any

PYTHON_WEB_DEPS: set[str] = {
    "flask", "django", "fastapi", "aiohttp", "tornado", "bottle",
    "pyramid", "sanic", "falcon", "starlette", "quart", "cherrypy",
    "hug", "masonite", "responder",
    "uvicorn", "gunicorn", "waitress", "daphne", "hypercorn",
    "uvicorn[standard]", "gunicorn[gevent]",
}

PYTHON_CLI_DEPS: set[str] = {
    "click", "typer", "cement", "cliff", "cleo", "invoke",
    "plac", "python-fire",
}

PYTHON_GUI_DEPS: set[str] = {
    "PyQt5", "PyQt6", "PySide2", "PySide6", "wxPython", "PyGTK",
    "Kivy", "DearPyGui", "pygame", "pyglet", "toga",
}


def _parse_requirements_txt(project_dir: Path) -> set[str]:
    req_file = project_dir / "requirements.txt"
    if not req_file.exists():
        return set()
    try:
        deps: set[str] = set()
        for line in req_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            m = re.match(r"^([a-zA-Z0-9_.-]+)", line)
            if m:
                deps.add(m.group(1).lower().rstrip(">===<~!@"))
        return deps
    except OSError:
        return set()


def _parse_pyproject_toml_deps(project_dir: Path) -> set[str]:
    pyproj = project_dir / "pyproject.toml"
    if not pyproj.exists():
        return set()
    try:
        deps: set[str] = set()
        content = pyproj.read_text()
        lines = content.splitlines()

        # PEP 621 format: [project] with dependencies = ["pkg", ...]
        # Also support: [tool.poetry.dependencies] and [project.dependencies]
        in_project = False
        in_deps_table = False
        in_deps_list = False
        bracket_depth = 0

        for line in lines:
            stripped = line.strip()

            # Section headers
            if re.match(r"^\[project\]$", stripped):
                in_project = True
                in_deps_table = False
                in_deps_list = False
                continue
            if re.match(r"^\[(project\.dependencies|tool\.poetry\.dependencies)\]$", stripped):
                in_deps_table = True
                in_project = False
                in_deps_list = False
                continue
            if re.match(r"^\[", stripped):
                in_project = False
                in_deps_table = False
                in_deps_list = False
                if not stripped.startswith("[tool."):
                    continue

            # Table dependencies: pkg = "^1.0" or pkg = {version = "^1.0"}
            if in_deps_table:
                m = re.match(r'([a-zA-Z0-9_.-]+)\s*=', stripped)
                if m:
                    pkg = m.group(1).lower().rstrip(">===<~!@")
                    if pkg not in ("python", "python-versions", "python-version"):
                        deps.add(pkg)

            # PEP 621 inline list under [project]
            if in_project:
                if "dependencies" in stripped and "[" in stripped:
                    in_deps_list = True
                    bracket_depth = stripped.count("[") - stripped.count("]")
                    # Extract from same line
                    m = re.findall(r'"([^"]+)"', stripped.split("[", 1)[1])
                    for d in m:
                        pkg = re.match(r"([a-zA-Z0-9_.-]+)", d)
                        if pkg:
                            deps.add(pkg.group(1).lower().rstrip(">===<~!@"))
                    if bracket_depth <= 0:
                        in_deps_list = False
                    continue
                if in_deps_list:
                    bracket_depth += stripped.count("[") - stripped.count("]")
                    m = re.findall(r'"([^"]+)"', stripped)
                    for d in m:
                        pkg = re.match(r"([a-zA-Z0-9_.-]+)", d)
                        if pkg:
                            deps.add(pkg.group(1).lower().rstrip(">===<~!@"))
                    if bracket_depth <= 0:
                        in_deps_list = False

        return deps
    except OSError:
        return set()


def _collect_python_deps(project_dir: Path) -> set[str]:
    return _parse_requirements_txt(project_dir) | _parse_pyproject_toml_deps(project_dir)


def _scan_python_source(project_dir: Path) -> dict[str, Any]:
    info: dict[str, Any] = {
        "has_http_server": False,
        "has_cli": False,
        "is_library": False,
        "has_desktop_gui": False,
    }
    http_patterns = [
        r"app\.run\s*\(", r"uvicorn\.run\s*\(", r"gunicorn",
        r"make_server\s*\(", r"application\.run\s*\(",
        r"web\.run\s*\(", r"sanic\.run\s*\(", r"aiohttp\.web",
        r"HTTPServer\s*\(", r"ThreadingHTTPServer\s*\(",
        r"django\.core\.management", r"DJANGO_SETTINGS_MODULE",
        r"masonite", r"flask\.Flask\s*\(", r"Flask\s*\(",
        r"FastAPI\s*\(", r"Starlette\s*\(", r"bottle\.run",
        r"tornado\.web\.Application",
    ]
    cli_patterns = [
        r"argparse\s*\.", r"ArgumentParser\s*\(", r"click\.(command|group|option)",
        r"typer\.", r"fire\.Fire\s*\(", r"cement",
    ]
    gui_patterns = [
        r"tkinter", r"PyQt5", r"PyQt6", r"PySide", r"wx\.Frame",
        r"kivy\.app", r"dearpygui", r"pygame",
    ]
    for pyfile in project_dir.rglob("*.py"):
        if pyfile.stat().st_size > 50000:
            continue
        try:
            content = pyfile.read_text(errors="replace")
            for pat in http_patterns:
                if re.search(pat, content):
                    info["has_http_server"] = True
                    break
            for pat in cli_patterns:
                if re.search(pat, content):
                    info["has_cli"] = True
                    break
            for pat in gui_patterns:
                if re.search(pat, content):
                    info["has_desktop_gui"] = True
                    break
        except (OSError, UnicodeDecodeError):
            continue
    return info
