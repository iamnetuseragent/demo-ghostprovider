"""GitHub repository analysis & hosting logic (demo version: limited to 3 services)."""

import json
import os
import random
import re
import socket
import subprocess
import tempfile
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import requests

from demo_ghostprovider.services import find_free_port
from demo_ghostprovider.state import register as _register_state

# ── Demo version: only these repositories are allowed ──
ALLOWED_REPOS = {
    "VERT-sh/VERT",
    "searxng/searxng",
    "usememos/memos",
}


@dataclass
class HostResult:
    service_names: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    healthy: bool = False
    errors: list[str] = field(default_factory=list)


GITHUB_URL_RE = re.compile(
    r"^https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$"
)


@dataclass
class RepoAnalysis:
    url: str = ""
    owner: str = ""
    name: str = ""
    exists: bool = False
    has_package_json: bool = False
    has_requirements: bool = False
    has_go_mod: bool = False
    has_cargo: bool = False
    has_index: bool = False
    language: str = ""
    can_host: bool = False
    reason: str = ""
    clone_path: str | None = None
    errors: list[str] = field(default_factory=list)
    app_category: str = "unknown"
    category_reason: str = ""
    web_app_verified: bool = True
    web_framework: str = ""
    has_http_server: bool = False
    has_cli: bool = False
    is_library: bool = False
    has_desktop_gui: bool = False
    host_score: int = 0
    host_recommendation: str = ""
    deep_analysis: dict[str, Any] = field(default_factory=dict)


def parse_github_url(url: str) -> tuple | None:
    m = GITHUB_URL_RE.match(url.strip())
    if m:
        return m.group(1), m.group(2).rstrip("/")
    return None


def _http_get_with_curl_fallback(url: str, timeout: int = 10,
                                  headers: dict[str, str] | None = None,
                                  retries: int = 2) -> requests.Response | None:
    """GET a URL with retry, falling back to curl when Python SSL is broken.

    Automatically injects a ``GITHUB_TOKEN`` (or ``GH_TOKEN``) for GitHub API
    calls so the 60-req/h unauthenticated rate limit is avoided.
    """
    import shutil
    headers = dict(headers) if headers else {}
    # Auto-inject GitHub token for api.github.com requests
    if "api.github.com" in url and "Authorization" not in headers:
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
        if token:
            headers["Authorization"] = f"token {token}"
    last_exc: Exception | None = None
    for attempt in range(1 + retries):
        try:
            r = requests.get(url, timeout=timeout, headers=headers)
            if r.status_code == 403 and attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            return r
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(1)
                continue
            if not shutil.which("curl"):
                raise
            break

    try:
        cmd = ["curl", "-s", "-f", "--max-time", str(timeout)]
        for k, v in headers.items():
            cmd += ["-H", f"{k}: {v}"]
        cmd.append(url)
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
        if proc.returncode == 0 and proc.stdout.strip():
            resp = requests.Response()
            resp.status_code = 200
            resp._content = proc.stdout.encode("utf-8")
            resp.encoding = "utf-8"
            return resp
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        if last_exc:
            raise last_exc
        return None


def fetch_repo_metadata(owner: str, name: str) -> tuple[dict[str, Any] | None, str | None]:
    """Fetch repo metadata from GitHub API.

    Returns (metadata, error_message).
    On success: (dict, None).
    On 404/private: (None, "not found").
    On network error: (None, "network error: <detail>").
    """
    try:
        r = _http_get_with_curl_fallback(
            f"https://api.github.com/repos/{owner}/{name}",
            headers={"Accept": "application/vnd.github.v3+json"},
        )
        if r is None:
            return (None, "Repository does not exist or is private")
        if r.status_code == 200:
            return (r.json(), None)
        if r.status_code == 404:
            return (None, "Repository does not exist or is private")
        if r.status_code == 403:
            return (None, "GitHub API rate limit exceeded — try again later or use a token")
        return (None, f"GitHub API returned HTTP {r.status_code}")
    except requests.ConnectionError as e:
        return (None, f"Network error: check your internet connection ({type(e).__name__})")
    except requests.Timeout:
        return (None, "Network error: request timed out")
    except requests.RequestException as e:
        return (None, f"Network error: {e}")


CATEGORY_KEYWORDS: dict[str, set[str]] = {
    "media_server": {
        "media", "music", "video", "stream", "streaming",
        "podcast", "audio", "photo", "gallery", "player",
        "jellyfin", "plex", "emby", "blackcandy", "navidrome",
        "airsonic", "funkwhale", "koel",
    },
    "web_app": {
        "web", "website", "frontend", "dashboard", "ui", "app",
        "server", "admin", "panel", "cms", "blog", "forum",
        "wiki", "board",
    },
    "api_server": {
        "api", "backend", "graphql", "rest", "grpc",
    },
    "search_engine": {
        "search", "searx", "searxng", "whoogle", "yacy",
        "librey", "shiori", "gigablast", "manticore",
    },
    "desktop_app": {
        "desktop", "electron", "gtk", "qt", "tui", "tauri",
        "nw.js", "react-native",
    },
    "cli": {
        "cli", "command-line", "console", "terminal",
    },
    "library": {
        "library", "sdk", "framework", "client", "sdk-",
        "plugin", "extension", "middleware",
    },
}

MEDIA_SERVER_INDICATORS: set[str] = CATEGORY_KEYWORDS["media_server"]
SEARCH_ENGINE_INDICATORS: set[str] = CATEGORY_KEYWORDS["search_engine"]
NOT_WEB_TOPICS: set[str] = {
    "desktop-app", "library", "cli", "command-line", "sdk",
    "react-native", "electron-app",
}

# ── Deep dependency & source analysis ──────────────────────────────

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

NODE_WEB_DEPS: set[str] = {
    "express", "next", "nuxt", "fastify", "koa", "hapi", "sails",
    "meteor", "restify", "feathers", "adonisjs", "loopback",
    "moleculer", "derby", "total.js",
    "@sveltejs/kit", "@angular/core", "@nestjs/core",
    "gatsby", "remix", "astro", "svelte", "vue", "react",
    "angular", "preact", "solid-js",
    "strapi", "keystone", "ghost", "directus", "payload",
    "next-server", "nuxt3", "vue-router",
}

NODE_CLI_DEPS: set[str] = {
    "commander", "yargs", "meow", "oclif", "vorpal", "ink",
}

NODE_GUI_DEPS: set[str] = {
    "electron", "electron-builder", "nw.js", "proton-native",
}

GO_WEB_DEPS: set[str] = {
    "gin", "fiber", "echo", "chi", "gorilla/mux", "beego",
    "revel", "buffalo", "iris", "httprouter", "negroni",
    "gin-gonic/gin", "gofiber/fiber", "labstack/echo", "go-chi/chi",
    "gorilla/mux",
}

GO_CLI_DEPS: set[str] = {
    "cobra", "urfave/cli", "pflag",
}

RUST_WEB_DEPS: set[str] = {
    "actix-web", "axum", "rocket", "warp", "tide", "salvo",
    "poem", "trillium", "nickel", "iron", "gotham", "tiny_http",
    "actix-rt",
}

RUST_CLI_DEPS: set[str] = {
    "clap", "structopt", "argh", "gumdrop",
}

RUST_GUI_DEPS: set[str] = {
    "tauri", "egui", "iced", "druid", "gtk",
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


def _collect_node_deps(project_dir: Path) -> dict[str, str] | None:
    pkg = project_dir / "package.json"
    if not pkg.exists():
        return None
    try:
        data = json.loads(pkg.read_text())
        all_deps: dict[str, str] = {}
        for section in ("dependencies", "devDependencies", "peerDependencies"):
            all_deps.update(data.get(section, {}))
        return all_deps
    except (json.JSONDecodeError, OSError):
        return None


def _collect_go_deps(project_dir: Path) -> set[str]:
    gomod = project_dir / "go.mod"
    if not gomod.exists():
        return set()
    try:
        deps: set[str] = set()
        for line in gomod.read_text().splitlines():
            # Formats:
            #   "    github.com/gin-gonic/gin v1.9.0"
            #   "require github.com/gin-gonic/gin v1.9.0"
            #   '	github.com/gin-gonic/gin v1.9.0'
            m = re.search(r'\s*([a-zA-Z0-9_.-]+(?:\/[a-zA-Z0-9_.-]+)*)\s+v', line)
            if m:
                full = m.group(1)
                deps.add(full)
                # Also add short form (strip known hosting prefixes)
                short = re.sub(r'^(github\.com|gopkg\.in|gitlab\.com|bitbucket\.org)/', '', full)
                if short != full:
                    deps.add(short)
        return deps
    except OSError:
        return set()


def _collect_rust_deps(project_dir: Path) -> set[str]:
    cargo = project_dir / "Cargo.toml"
    if not cargo.exists():
        return set()
    try:
        deps: set[str] = set()
        content = cargo.read_text()
        in_deps = False
        for line in content.splitlines():
            if re.match(r"^\[dependencies\]", line):
                in_deps = True
                continue
            if in_deps:
                if re.match(r"^\[", line):
                    break
                m = re.match(r'([a-zA-Z0-9_-]+)\s*=', line.strip())
                if m:
                    deps.add(m.group(1).lower().replace("_", "-"))
        return deps
    except OSError:
        return set()


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


def _scan_node_source(project_dir: Path) -> dict[str, Any]:
    info: dict[str, Any] = {
        "has_http_server": False,
        "has_cli": False,
        "is_library": False,
        "has_desktop_gui": False,
    }
    http_patterns = [
        r"app\.listen\s*\(", r"server\.listen\s*\(", r"createServer\s*\(",
        r"http\.createServer", r"express\s*\(", r"Fastify\s*\(",
        r"Koa\s*\(", r"socket\.io",
    ]
    cli_patterns = [
        r"commander", r"yargs", r"argv", r"process\.argv",
        r"meow\s*\(", r"oclif",
    ]
    gui_patterns = [
        r"electron", r"nw\.js", r"BrowserWindow",
    ]
    for jsfile in list(project_dir.rglob("*.js")) + list(project_dir.rglob("*.ts")):
        if jsfile.stat().st_size > 100000:
            continue
        if "node_modules" in str(jsfile):
            continue
        try:
            content = jsfile.read_text(errors="replace")
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


def _scan_go_source(project_dir: Path) -> dict[str, Any]:
    info: dict[str, Any] = {
        "has_http_server": False,
        "has_cli": False,
        "is_library": False,
        "has_desktop_gui": False,
    }
    http_patterns = [
        r"http\.ListenAndServe", r"http\.ListenAndServeTLS",
        r"gin\.Default\s*\(", r"gin\.New\s*\(", r"fiber\.New\s*\(",
        r"echo\.New\s*\(", r"chi\.NewRouter", r"mux\.NewRouter",
        r"beego\.Run", r"iris\.New", r"buffalo",
    ]
    cli_patterns = [
        r"cobra\.Command", r"cobra\.Execute", r"flag\.",
        r"pflag\.", r"cli\.App",
    ]
    for gofile in project_dir.rglob("*.go"):
        if gofile.stat().st_size > 100000:
            continue
        try:
            content = gofile.read_text(errors="replace")
            for pat in http_patterns:
                if re.search(pat, content):
                    info["has_http_server"] = True
                    break
            for pat in cli_patterns:
                if re.search(pat, content):
                    info["has_cli"] = True
                    break
        except (OSError, UnicodeDecodeError):
            continue
    return info


def _scan_rust_source(project_dir: Path) -> dict[str, Any]:
    info: dict[str, Any] = {
        "has_http_server": False,
        "has_cli": False,
        "is_library": False,
        "has_desktop_gui": False,
    }
    http_patterns = [
        r"actix_web::", r"axum::", r"rocket::", r"warp::filter",
        r"warp::path", r"Tide::new", r"salvo::",
        r"HttpServer::new", r"Server::bind",
    ]
    cli_patterns = [
        r"clap::", r"StructOpt", r"argh::",
    ]
    for rsfile in project_dir.rglob("*.rs"):
        if rsfile.stat().st_size > 100000:
            continue
        try:
            content = rsfile.read_text(errors="replace")
            for pat in http_patterns:
                if re.search(pat, content):
                    info["has_http_server"] = True
                    break
            for pat in cli_patterns:
                if re.search(pat, content):
                    info["has_cli"] = True
                    break
        except (OSError, UnicodeDecodeError):
            continue
    return info


def _is_library_project(project_dir: Path, analysis: RepoAnalysis) -> bool:
    """Heuristic: project looks like a library (not an application)."""
    if analysis.language == "Python":
        has_setup = (project_dir / "setup.py").exists() or (project_dir / "setup.cfg").exists()
        has_pyproject = (project_dir / "pyproject.toml").exists()
        has_entry = (project_dir / "__main__.py").exists() or any(
            (project_dir / f).exists()
            for f in ("app.py", "main.py", "server.py", "manage.py", "wsgi.py", "asgi.py", "run.py")
        )
        has_src = (project_dir / "src").is_dir()
        if has_setup and not has_entry:
            return True
        if has_pyproject and not has_entry and has_src:
            return True
    if analysis.language == "Node.js":
        pkg_json = project_dir / "package.json"
        if pkg_json.exists():
            try:
                pkg = json.loads(pkg_json.read_text())
                has_main = bool(pkg.get("main"))
                has_bin = bool(pkg.get("bin"))
                has_scripts = bool(pkg.get("scripts"))
                has_web_app_sections = any(
                    s in str(pkg) for s in ("react", "next", "nuxt", "express", "angular")
                )
                is_pure_lib = has_main and not has_bin and not has_scripts
                return is_pure_lib and not has_web_app_sections
            except (json.JSONDecodeError, OSError):
                pass
    if analysis.language == "Rust":
        cargo = project_dir / "Cargo.toml"
        has_main = (project_dir / "src" / "main.rs").exists()
        has_lib = (project_dir / "src" / "lib.rs").exists()
        if cargo.exists():
            try:
                content = cargo.read_text()
                if "[lib]" in content and "[[bin]]" not in content:
                    return True
                # Has lib.rs but no main.rs → library
                if has_lib and not has_main:
                    return True
            except OSError:
                pass
    if analysis.language == "Go":
        main_go = project_dir / "main.go"
        if not main_go.exists():
            has_main_func = False
            for gofile in project_dir.rglob("*.go"):
                if gofile.stat().st_size > 10000:
                    continue
                try:
                    if "func main()" in gofile.read_text(errors="replace"):
                        has_main_func = True
                        break
                except OSError:
                    continue
            if not has_main_func:
                return True
    return False


def detect_app_category(
    analysis: RepoAnalysis,
    metadata: dict[str, Any] | None,
) -> tuple[str, str, bool]:
    description = (metadata or {}).get("description", "") or ""
    topics = set((metadata or {}).get("topics", []) or [])
    name_lower = analysis.name.lower()
    desc_lower = description.lower()
    combined = f"{name_lower} {desc_lower}"

    desc_kws = {"desktop", "electron", "cli", "command line", "terminal",
                "library", "sdk", "framework"}
    media_kws = {"media", "music", "video", "stream", "streaming",
                 "podcast", "audio", "photo", "gallery", "player",
                 "jellyfin", "plex", "emby", "blackcandy", "navidrome",
                 "airsonic", "funkwhale", "koel"}
    search_kws = {"search", "searx", "searxng", "whoogle", "yacy",
                  "librey", "shiori", "gigablast"}
    web_kws = {"web", "website", "frontend", "dashboard", "ui", "app",
               "server", "admin", "panel", "cms", "blog", "forum",
               "wiki", "board", "api", "backend", "graphql", "rest"}

    # ── Phase 1: Deep analysis signals (most reliable) ──
    if analysis.deep_analysis:
        da = analysis.deep_analysis
        # Known self-hosted services (highest priority)
        if da.get("is_openwebui"):
            return "web_app", "Deep analysis: Open WebUI AI interface", True
        # Web signals first (strongest indicators)
        if da.get("web_framework"):
            return "web_app", f"Deep analysis: {da['web_framework']}", True
        if da.get("has_http_server"):
            return "web_app", "Deep analysis: HTTP server code found", True
        # Non-web signals (only if no web signal present)
        if da.get("has_desktop_gui"):
            return "desktop_app", "Deep analysis: GUI framework detected", False
        if da.get("has_cli") and not da.get("has_http_server"):
            return "cli", "Deep analysis: CLI interface detected", False
        if da.get("is_library"):
            return "library", "Deep analysis: project identified as library", False

    # ── Phase 2: GitHub metadata (fast, no clone needed) ──
    for kw in desc_kws:
        if kw in desc_lower or kw in name_lower:
            if kw in ("library", "sdk", "framework"):
                return "library", f"GitHub description/library keyword: {kw}", False
            if kw in ("desktop", "electron"):
                return "desktop_app", f"GitHub description: {kw}", False
            if kw in ("cli", "command line", "terminal"):
                return "cli", f"GitHub description: {kw}", False
    if topics & NOT_WEB_TOPICS:
        topic_str = ", ".join(sorted(topics & NOT_WEB_TOPICS))
        if topics & {"desktop-app", "electron-app"}:
            return "desktop_app", f"GitHub topics: {topic_str}", False
        return "library", f"GitHub topics: {topic_str}", False

    search_topics = {"search-engine", "search", "searx", "searxng", "whoogle", "yacy"}
    if topics & search_topics:
        topic_str = ", ".join(sorted(topics & search_topics))
        return "search_engine", f"GitHub topics: {topic_str}", True

    for kw in search_kws:
        if kw in combined:
            return "search_engine", f"Keyword: {kw}", True
    for kw in media_kws:
        if kw in combined:
            return "media_server", f"Keyword: {kw}", True
    for kw in web_kws:
        if kw in combined:
            return "web_app", f"Keyword: {kw}", True

    # ── Phase 3: File-level fallback ──
    if analysis.has_index:
        return "web_app", "Static site (index.html)", True

    return "unknown", "Could not determine application type from available signals", True


def _deep_analyze_project(analysis: RepoAnalysis) -> RepoAnalysis:
    """Run deep, dependency & source-code-level analysis on a cloned project.

    Examines dependency files (requirements.txt, package.json, go.mod,
    Cargo.toml) and scans source code for HTTP servers, CLI interfaces,
    GUI frameworks, and library patterns.
    """
    if not analysis.clone_path:
        return analysis

    project_dir = Path(analysis.clone_path)
    da: dict[str, Any] = {
        "web_framework": "",
        "has_http_server": False,
        "has_cli": False,
        "is_library": False,
        "has_desktop_gui": False,
        "gui_dep": False,
        "gh_description_web": False,
        "gh_topics_media": False,
        "github_not_web": False,
    }

    # ── 1. Dependency analysis ──
    if analysis.has_requirements:
        py_deps = _collect_python_deps(project_dir)
        web_dep = (py_deps & PYTHON_WEB_DEPS)
        cli_dep = (py_deps & PYTHON_CLI_DEPS)
        gui_dep = (py_deps & PYTHON_GUI_DEPS)
        if web_dep:
            da["web_framework"] = next(iter(web_dep))
        if cli_dep:
            da["has_cli"] = True
        if gui_dep:
            da["has_desktop_gui"] = True
            da["gui_dep"] = True
        da["_py_deps"] = py_deps

    if analysis.has_package_json:
        nd = _collect_node_deps(project_dir)
        if nd:
            all_dep_names = set(nd.keys())
            web_dep = all_dep_names & NODE_WEB_DEPS
            cli_dep = all_dep_names & NODE_CLI_DEPS
            gui_dep = all_dep_names & NODE_GUI_DEPS
            if web_dep:
                wf = next(iter(web_dep))
                da["web_framework"] = wf
                if "/" in wf:
                    da["web_framework"] = wf.split("/")[-1]
            if cli_dep:
                da["has_cli"] = True
            if gui_dep:
                da["has_desktop_gui"] = True
                da["gui_dep"] = True
        da["_node_deps"] = nd

    if analysis.has_go_mod:
        go_deps = _collect_go_deps(project_dir)
        web_dep = go_deps & GO_WEB_DEPS
        cli_dep = go_deps & GO_CLI_DEPS
        if web_dep:
            wf = next(iter(web_dep))
            short = wf.split("/")[-1] if "/" in wf else wf
            da["web_framework"] = short
        if cli_dep:
            da["has_cli"] = True
        da["_go_deps"] = go_deps

    if analysis.has_cargo:
        rs_deps = _collect_rust_deps(project_dir)
        web_dep = rs_deps & RUST_WEB_DEPS
        cli_dep = rs_deps & RUST_CLI_DEPS
        gui_dep = rs_deps & RUST_GUI_DEPS
        if web_dep:
            da["web_framework"] = next(iter(web_dep))
        if cli_dep:
            da["has_cli"] = True
        if gui_dep:
            da["has_desktop_gui"] = True
            da["gui_dep"] = True
        da["_rs_deps"] = rs_deps

    # ── 2. Source code scanning ──
    if analysis.language == "Python":
        src_info = _scan_python_source(project_dir)
    elif analysis.language == "Node.js":
        src_info = _scan_node_source(project_dir)
    elif analysis.language == "Go":
        src_info = _scan_go_source(project_dir)
    elif analysis.language == "Rust":
        src_info = _scan_rust_source(project_dir)
    else:
        src_info = {}

    for key in ("has_http_server", "has_cli", "has_desktop_gui"):
        if src_info.get(key):
            da[key] = True

    # ── 3. Library detection ──
    da["is_library"] = _is_library_project(project_dir, analysis)

    # ── 4. AFFiNE detection (self-hosted server) ──
    da["is_affine"] = False
    if analysis.has_package_json:
        pkg = _read_package_json(project_dir)
        if pkg:
            pkg_name = pkg.get("name", "")
            # Check for AFFiNE monorepo
            if pkg_name == "@affine/monorepo":
                da["is_affine"] = True
            # Check for AFFiNE server package
            elif pkg_name == "@affine/server":
                da["is_affine"] = True
            # Check by yarn 4 + AFFiNE-specific paths
            elif (project_dir / ".yarn" / "releases").is_dir():
                if (project_dir / "packages" / "backend" / "server").is_dir():
                    da["is_affine"] = True

    # ── 5. Open WebUI detection (self-hosted AI interface) ──
    da["is_openwebui"] = False
    if analysis.name and analysis.name.lower() in ("open-webui", "openwebui"):
        da["is_openwebui"] = True
    elif analysis.owner and analysis.owner.lower() == "open-webui":
        da["is_openwebui"] = True
    # Check for Open WebUI project structure: backend/open_webui/main.py
    if (project_dir / "backend" / "open_webui" / "main.py").exists():
        da["is_openwebui"] = True
    # Also check package.json name
    if analysis.has_package_json:
        pkg = _read_package_json(project_dir)
        if pkg and pkg.get("name", "") == "open-webui":
            da["is_openwebui"] = True

    # ── 6. Store in analysis ──
    analysis.deep_analysis = da
    analysis.web_framework = da.get("web_framework", "")
    analysis.has_http_server = da.get("has_http_server", False)
    analysis.has_cli = da.get("has_cli", False)
    analysis.is_library = da.get("is_library", False)
    analysis.has_desktop_gui = da.get("has_desktop_gui", False)

    return analysis


def _check_root_files_via_api(owner: str, name: str) -> set[str] | None:
    """Fetch root directory listing via GitHub Contents API (no clone needed)."""
    try:
        r = _http_get_with_curl_fallback(
            f"https://api.github.com/repos/{owner}/{name}/contents/",
            headers={"Accept": "application/vnd.github.v3+json"},
        )
        if r is None or r.status_code != 200:
            return None
        return {item["name"] for item in r.json() if isinstance(item, dict)}
    except (requests.RequestException, ValueError, TypeError):
        return None


def analyze_repo(url: str, work_dir: str | None = None) -> RepoAnalysis:
    result = RepoAnalysis(url=url)

    parsed = parse_github_url(url)
    if not parsed:
        result.errors.append("Invalid GitHub URL format")
        result.reason = "Invalid GitHub URL"
        return result

    result.owner, result.name = parsed

    # ── Demo version: restrict to allowed repositories only ──
    repo_key = f"{result.owner}/{result.name}"
    if repo_key not in ALLOWED_REPOS:
        result.errors.append(
            f"Demo version: only VERT, SearXNG, and Memos are supported"
        )
        result.reason = (
            f"'{repo_key}' is not supported in the demo version.\n"
            "Allowed repositories:\n"
            "  • https://github.com/VERT-sh/VERT\n"
            "  • https://github.com/searxng/searxng\n"
            "  • https://github.com/usememos/memos"
        )
        return result

    metadata, meta_error = fetch_repo_metadata(result.owner, result.name)
    if metadata is None:
        metadata = {}
        result.errors.append(meta_error or "Repository not found")
    result.exists = True

    # Quick API-based root file check (avoids slow clone)
    root_files = _check_root_files_via_api(result.owner, result.name)
    if root_files is not None:
        result.has_package_json = "package.json" in root_files
        result.has_requirements = "requirements.txt" in root_files
        result.has_go_mod = "go.mod" in root_files
        result.has_cargo = "Cargo.toml" in root_files
        result.has_index = any(f in root_files for f in ("index.html", "index.htm", "index.php"))

    # Clone for deeper analysis
    already_cloned = False
    if work_dir:
        base = os.path.abspath(os.path.expanduser(work_dir))
    else:
        # Default to ~/localhosts instead of /tmp to avoid tmpfs disk quota issues
        base = os.path.expanduser("~/localhosts")
    os.makedirs(base, exist_ok=True)
    clone_dir = os.path.join(base, result.name)
    # Reuse existing valid clone instead of deleting and re-cloning
    if os.path.isdir(os.path.join(clone_dir, ".git")):
        already_cloned = True
    elif os.path.isdir(clone_dir):
        shutil.rmtree(clone_dir, ignore_errors=True)

    if not already_cloned:
        git_url = f"https://github.com/{result.owner}/{result.name}.git"
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
        if token:
            git_url = git_url.replace("https://", f"https://x-access-token:{token}@")
        if not _git_clone(git_url, clone_dir):
            result.errors.append("git clone failed after retries (check network connection)")
            result.reason = "Cannot clone repository"
            return result

    result.clone_path = clone_dir
    items = os.listdir(clone_dir)

    result.has_package_json = "package.json" in items
    result.has_requirements = "requirements.txt" in items
    result.has_go_mod = "go.mod" in items
    result.has_cargo = "Cargo.toml" in items
    result.has_index = any(f in items for f in ("index.html", "index.htm", "index.php"))

    result.language = _detect_language(result)

    # Deep analysis: dependency scanning + source code analysis
    result = _deep_analyze_project(result)

    # Compute hosting score and verdict
    result.can_host, result.reason = _can_host_verdict(result)

    # Clone succeeded — clear API errors since we analyzed from files
    result.errors = [e for e in result.errors if e != (meta_error or "Repository not found")]

    # Category detection from metadata
    if metadata:
        desc = (metadata.get("description") or "").lower()
        topics = set(metadata.get("topics", []) or [])
        not_web_kws = {"desktop", "electron", "cli", "command line", "terminal", "library", "sdk", "framework"}
        if any(kw in desc for kw in not_web_kws):
            if not result.deep_analysis:
                result.deep_analysis = {}
            result.deep_analysis["github_not_web"] = True
        web_kws = {"web", "website", "frontend", "dashboard", "api", "server", "backend"}
        if any(kw in desc for kw in web_kws):
            if not result.deep_analysis:
                result.deep_analysis = {}
            result.deep_analysis["gh_description_web"] = True
        media_topics = {"media-server", "music", "streaming", "jellyfin", "plex"}
        if topics & media_topics:
            if not result.deep_analysis:
                result.deep_analysis = {}
            result.deep_analysis["gh_topics_media"] = True
        search_topics = {"search-engine", "searx", "searxng", "whoogle", "yacy", "search"}
        if topics & search_topics:
            if not result.deep_analysis:
                result.deep_analysis = {}
            result.deep_analysis["gh_topics_search"] = True
    cat, cat_reason, is_web = detect_app_category(result, metadata)
    result.app_category = cat
    result.category_reason = cat_reason
    result.web_app_verified = is_web

    return result


def ensure_cloned(analysis: RepoAnalysis, work_dir: str | None = None) -> None:
    """Clone the repo if not already cloned (deferred from quick analysis)."""
    if analysis.clone_path is not None:
        return
    if not analysis.exists or not analysis.owner or not analysis.name:
        return

    if work_dir:
        base = os.path.abspath(os.path.expanduser(work_dir))
    else:
        # Default to ~/localhosts instead of /tmp to avoid tmpfs disk quota issues
        base = os.path.expanduser("~/localhosts")
    os.makedirs(base, exist_ok=True)
    clone_dir = os.path.join(base, analysis.name)
    # Reuse existing valid clone
    if os.path.isdir(os.path.join(clone_dir, ".git")):
        analysis.clone_path = clone_dir
        items = os.listdir(clone_dir)
        analysis.has_package_json = "package.json" in items
        analysis.has_requirements = "requirements.txt" in items
        analysis.has_go_mod = "go.mod" in items
        analysis.has_cargo = "Cargo.toml" in items
        analysis.has_index = any(f in items for f in ("index.html", "index.htm", "index.php"))
        return
    elif os.path.isdir(clone_dir):
        shutil.rmtree(clone_dir, ignore_errors=True)

    git_url = f"https://github.com/{analysis.owner}/{analysis.name}.git"
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    if token:
        git_url = git_url.replace("https://", f"https://x-access-token:{token}@")

    if not _git_clone(git_url, clone_dir):
        raise RuntimeError("git clone failed after retries (check network connection)")

    analysis.clone_path = clone_dir
    items = os.listdir(clone_dir)
    analysis.has_package_json = "package.json" in items
    analysis.has_requirements = "requirements.txt" in items
    analysis.has_go_mod = "go.mod" in items
    analysis.has_cargo = "Cargo.toml" in items
    analysis.has_index = any(f in items for f in ("index.html", "index.htm", "index.php"))


def preflight_check() -> list[str]:
    """Run pre-flight checks before deployment. Returns list of issues."""
    issues: list[str] = []

    # systemd running
    try:
        r = subprocess.run(
            ["systemctl", "--user", "is-system-running"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0 and "degraded" not in r.stdout:
            issues.append("systemd not running properly")
    except FileNotFoundError:
        issues.append("systemd not found")
    except subprocess.TimeoutExpired:
        issues.append("systemd not responding")

    # systemd-nspawn available
    try:
        r = subprocess.run(
            ["systemd-nspawn", "--version"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0:
            issues.append("systemd-nspawn not available")
    except FileNotFoundError:
        issues.append("systemd-nspawn not installed")
    except subprocess.TimeoutExpired:
        issues.append("systemd-nspawn not responding")

    # Network
    try:
        r = subprocess.run(
            ["ping", "-c", "1", "-W", "2", "8.8.8.8"],
            capture_output=True, timeout=5,
        )
        if r.returncode != 0:
            issues.append("No network connectivity")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        issues.append("No network connectivity")

    return issues


def _detect_language(analysis: RepoAnalysis) -> str:
    if analysis.has_package_json:
        return "Node.js"
    if analysis.has_requirements:
        return "Python"
    if analysis.has_go_mod:
        return "Go"
    if analysis.has_cargo:
        return "Rust"
    if analysis.has_index:
        return "Static HTML"
    return "Unknown"


def _compute_host_score(analysis: RepoAnalysis) -> tuple[int, str]:
    """Compute a confidence score (0-100) and recommendation for hosting.

    Positive signals (web app indicators):
      +50  Web framework detected in dependencies
      +40  HTTP server code found in source
      +30  Static site (index.html)
      +20  index.html present
      +15  has_package_json with web deps
      +10  Python with web deps
      +10  GitHub description/web keywords
      +10  media_server keywords
      +10  search_engine keywords
      +25  searx (special known service)

    Negative signals (non-web indicators):
      -40  CLI dependency without web dep
      -50  Library structure (no entry point)
      -50  Desktop/GUI dependency
      -40  CLI source code without HTTP server
      -30  GitHub desktop/CLI keywords
    """
    score = 0
    reasons: list[str] = []
    da = analysis.deep_analysis or {}

    # ── Dependency-level signals ──
    wf = da.get("web_framework", "")
    if wf:
        score += 50
        reasons.append(f"web framework: {wf} (+50)")

    # ── Source code signals ──
    if da.get("has_http_server"):
        score += 40
        reasons.append("HTTP server in source (+40)")

    html_count = 0
    if analysis.clone_path:
        html_count = len(list(Path(analysis.clone_path).rglob("*.html")))

    if analysis.has_index:
        score += 30
        reasons.append("static index.html (+30)")
    elif html_count > 0:
        score += min(15 + html_count, 30)
        reasons.append(f"HTML files ({html_count}) (+{min(15 + html_count, 30)})")

    # ── Language-specific dep signals (use cached data if available) ──
    if analysis.has_package_json:
        nd = da.get("_node_deps") if da.get("_node_deps") is not None else (
            _collect_node_deps(Path(analysis.clone_path)) if analysis.clone_path else None
        )
        if nd:
            web_in_node = set(nd.keys()) & NODE_WEB_DEPS
            if web_in_node:
                score += 15
                reasons.append(f"Node.js web deps: {', '.join(web_in_node)} (+15)")
    if analysis.has_requirements or (analysis.clone_path and (Path(analysis.clone_path) / "requirements.txt").exists()):
        pd = da.get("_py_deps") if da.get("_py_deps") is not None else (
            _collect_python_deps(Path(analysis.clone_path)) if analysis.clone_path else set()
        )
        web_in_py = pd & PYTHON_WEB_DEPS
        if web_in_py:
            score += 10
            reasons.append(f"Python web deps: {', '.join(web_in_py)} (+10)")

    # ── Known service signals ──
    if analysis.name and "searx" in analysis.name.lower():
        score += 25
        reasons.append("SearXNG search engine (+25)")
    if analysis.name and any(kw in analysis.name.lower() for kw in ("whoogle", "yacy", "librey")):
        score += 20
        reasons.append("search engine detected (+20)")
    if da.get("is_openwebui"):
        score += 50
        reasons.append("Open WebUI self-hosted AI interface (+50)")

    # ── GitHub metadata ──
    if da.get("gh_description_web"):
        score += 10
        reasons.append("GitHub description suggests web app (+10)")
    if da.get("gh_topics_media"):
        score += 10
        reasons.append("GitHub topics suggest media server (+10)")
    if da.get("gh_topics_search"):
        score += 10
        reasons.append("GitHub topics suggest search engine (+10)")

    # ── Negative signals (only if no strong web presence) ──
    has_strong_web = bool(wf) or da.get("has_http_server")

    if not has_strong_web:
        if da.get("has_desktop_gui") or (da.get("gui_dep")):
            score -= 50
            reasons.append("desktop/GUI detected (-50)")
        if da.get("is_library"):
            score -= 50
            reasons.append("project structure is a library (-50)")
        if da.get("has_cli") and not da.get("has_http_server"):
            score -= 40
            reasons.append("CLI interface without HTTP server (-40)")
        if da.get("github_not_web"):
            score -= 30
            reasons.append("GitHub metadata suggests non-web (-30)")

        # ── CLI deps without web deps ──
        if analysis.clone_path:
            if analysis.has_package_json:
                nd = da.get("_node_deps") if da.get("_node_deps") is not None else _collect_node_deps(Path(analysis.clone_path))
                if nd:
                    has_web = bool(set(nd.keys()) & NODE_WEB_DEPS)
                    has_cli = bool(set(nd.keys()) & NODE_CLI_DEPS)
                    if has_cli and not has_web:
                        score -= 40
                        reasons.append("Node CLI deps without web deps (-40)")
            pd = da.get("_py_deps") if da.get("_py_deps") is not None else (
                _collect_python_deps(Path(analysis.clone_path)) if analysis.clone_path else set()
            )
            has_py_cli = bool(pd & PYTHON_CLI_DEPS)
            has_py_web = bool(pd & PYTHON_WEB_DEPS)
            if has_py_cli and not has_py_web:
                score -= 40
                reasons.append("Python CLI deps without web deps (-40)")
            has_py_gui = bool(pd & PYTHON_GUI_DEPS)
            if has_py_gui:
                score -= 50
                reasons.append("Python GUI deps (-50)")

    # ── Score-based verdict ──
    if score >= 50:
        return score, f"high confidence ({score}/100): " + "; ".join(reasons[:3])
    elif score >= 20:
        return score, f"low confidence ({score}/100): " + "; ".join(reasons[:3])
    else:
        return score, f"unsuitable ({score}/100): " + "; ".join(reasons[:3] if reasons else ["no web indicators found"])


def _can_host_verdict(analysis: RepoAnalysis) -> tuple[bool, str]:
    score, rec = _compute_host_score(analysis)
    analysis.host_score = score
    analysis.host_recommendation = rec
    if score >= 50:
        return True, rec
    if score >= 20:
        return True, f"LOW CONFIDENCE — {rec}"
    return False, rec


def find_free_port(start: int = 0, max_tries: int = 50) -> int:
    """Find the first available port starting from `start`.
    
    If start is 0, picks a random port in [8000, 32768) to reduce
    collisions with commonly-used ports like 3000 or 8080.
    """
    if start == 0:
        start = random.randint(8000, 30000)
    for port in range(start, start + max_tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError(f"No free port found in range {start}-{start + max_tries}")


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def _get_service_logs(service_name: str, lines: int = 50) -> str:
    """Get recent logs from a systemd service."""
    try:
        r = subprocess.run(
            ["journalctl", "-u", service_name, "-n", str(lines), "--no-pager"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return ""


def _discover_service_urls(service_name: str) -> list[str]:
    """Discover HTTP URLs for a systemd service."""
    urls: list[str] = []

    # Get the main PID
    try:
        r = subprocess.run(
            ["systemctl", "--user", "show", service_name, "--property=MainPID", "--value"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip() != "0":
            pid = r.stdout.strip()
            # Find listening ports for this PID
            r2 = subprocess.run(
                ["ss", "-tlnp", f"pid={pid}"],
                capture_output=True, text=True, timeout=5,
            )
            if r2.returncode == 0:
                for line in r2.stdout.strip().split("\n")[1:]:
                    parts = line.split()
                    if len(parts) >= 4:
                        addr_port = parts[3]
                        if ":" in addr_port:
                            port_str = addr_port.rsplit(":", 1)[-1]
                            if port_str.isdigit():
                                urls.append(f"http://localhost:{port_str}")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return urls


def verify_deployment(result: HostResult, timeout: int = 300,
                      on_status: Callable[[str], None] | None = None) -> HostResult:
    done_callback = on_status or (lambda _: None)

    if not result.service_names:
        result.errors.append("No services to verify")
        return result

    # Wait for services to be active
    deadline = time.time() + timeout
    for service_name in result.service_names:
        while time.time() < deadline:
            try:
                r = subprocess.run(
                    ["systemctl", "--user", "is-active", service_name],
                    capture_output=True, text=True, timeout=5,
                )
                status = r.stdout.strip()
                if status == "active":
                    done_callback(f"service {service_name} is active")
                    break
                if status in ("failed", "inactive"):
                    logs = _get_service_logs(service_name, 20)
                    result.errors.append(f"Service {service_name} {status}: {logs[:200]}")
                    break
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass
            time.sleep(2)
        else:
            result.errors.append(f"Service {service_name} not active after {timeout}s")

    # If no URLs discovered yet, try to find them from service ports
    if not result.urls:
        for service_name in result.service_names:
            result.urls.extend(_discover_service_urls(service_name))

    if not result.urls:
        result.errors.append("No exposed ports found — service may not be a web service")
        for service_name in result.service_names:
            logs = _get_service_logs(service_name)
            if logs:
                result.errors.append(f"Service {service_name} logs:\n{logs[:300]}")
                break
        return result

    # Verify URLs with adaptive retries (exponential backoff, up to timeout)
    for url in result.urls:
        ok = False
        detail = ""
        retries = 0
        while time.time() < deadline and retries < 30:
            done_callback(f"checking {url} (attempt {retries + 1})...")
            ok, detail = verify_url(url, timeout=10)
            if ok:
                result.healthy = True
                done_callback(f"{url} is responding")
                break
            retries += 1
            sleep_time = min(5 * retries, 30)
            time.sleep(sleep_time)
        if ok:
            break
        result.healthy = False
        done_callback(f"health check failed for {url}")

    if not result.healthy:
        for service_name in result.service_names:
            logs = _get_service_logs(service_name)
            if logs:
                result.errors.append(f"Service {service_name} logs:\n{logs[:500]}")
                break
        if result.urls:
            result.errors.append(f"Health check failed for {result.urls}: {detail}")

    return result


def _strategy_priority(analysis: RepoAnalysis) -> list[str]:
    """Return strategy names ordered by priority for the given analysis."""
    da = analysis.deep_analysis or {}
    wf = da.get("web_framework", "")

    # ── AFFiNE: special self-hosted server deployment ──
    if analysis.deep_analysis.get("is_affine"):
        return ["Affine"]

    # ── Open WebUI: special self-hosted AI interface deployment ──
    if analysis.deep_analysis.get("is_openwebui"):
        return ["OpenWebUI"]

    # If a web framework is detected in Python deps, prefer Python strategy
    if analysis.has_requirements and wf:
        strategies = ["Python"]
        if analysis.has_go_mod:
            strategies.append("Go")
        if analysis.has_package_json:
            strategies.append("Node.js")
        if analysis.has_cargo:
            strategies.append("Rust")
        if analysis.has_index:
            strategies.append("Static")
        return strategies

    # Default: Go > Node.js > Rust > Python > Static
    # Node.js is preferred over Rust when both exist (monorepos with Cargo.toml
    # for WASM/native bindings but primary app is Node.js, e.g. AFFiNE)
    if analysis.has_go_mod:
        return ["Go"]
    if analysis.has_package_json:
        if analysis.has_cargo:
            return ["Node.js", "Rust"]
        return ["Node.js"]
    if analysis.has_cargo:
        return ["Rust"]
    if analysis.has_requirements:
        return ["Python"]
    if analysis.has_index:
        return ["Static"]
    return []


def _prepare_searxng_config(project_dir: Path, port: int = 8888) -> None:
    """Patch SearXNG settings.yml with a real secret_key, bind address, and port."""
    settings_file = project_dir / "searx" / "settings.yml"
    if not settings_file.exists():
        return

    import secrets as _secrets
    secret_key = _secrets.token_hex(32)

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


def _git_env() -> dict[str, str]:
    """Return environment dict with git configured for reliable cloning.

    Uses curl as HTTP backend to avoid TLS handshake issues with git's
    built-in HTTP client on some network configurations.
    """
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
    # Timeout per attempt: 10 minutes (large repos like AFFiNE need time)
    timeout = 600

    # Clone strategies in order of preference
    clone_strategies = [
        # Fastest: shallow, single-branch, no tags
        ["git", "clone", "--depth", "1", "--single-branch", "--no-tags", url, dest],
        # Full clone (most reliable, slower)
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
            # Log the error for debugging
            err = proc.stderr.strip()
            if err:
                _emit(f"git error: {err[:120]}")
        except subprocess.TimeoutExpired:
            _emit(f"clone timed out after {timeout}s")
        except OSError as e:
            _emit(f"clone OS error: {e}")

        # Clean up failed attempt
        if os.path.isdir(dest):
            shutil.rmtree(dest, ignore_errors=True)

        # Exponential backoff: 5s, 15s, 45s, 135s, 405s
        wait = min(5 * (3 ** attempt), 300)
        _emit(f"retrying in {wait}s...")
        time.sleep(wait)

    # ── Fallback: download tarball via curl ──
    _emit("git clone failed, trying tarball download...")
    tarball_url = url.replace("https://github.com/", "https://github.com/")
    if not tarball_url.endswith("/"):
        tarball_url += "/"
    tarball_url += "archive/refs/heads/master.tar.gz"
    # Also try 'main' branch
    tarball_urls = [tarball_url, tarball_url.replace("/master.", "/main.")]

    for tb_url in tarball_urls:
        try:
            _emit(f"downloading tarball...")
            proc = subprocess.run(
                ["curl", "-4", "-sL", "-o", "/tmp/_gp_tarball.tar.gz", tb_url],
                capture_output=True, text=True, timeout=300,
            )
            if proc.returncode == 0 and os.path.getsize("/tmp/_gp_tarball.tar.gz") > 1000:
                _emit("extracting tarball...")
                os.makedirs(dest, exist_ok=True)
                proc2 = subprocess.run(
                    ["tar", "xzf", "/tmp/_gp_tarball.tar.gz", "-C", dest, "--strip-components=1"],
                    capture_output=True, text=True, timeout=120,
                )
                os.remove("/tmp/_gp_tarball.tar.gz")
                if proc2.returncode == 0:
                    # Initialize git dir so analysis works
                    subprocess.run(["git", "init"], capture_output=True, cwd=dest, timeout=10)
                    _emit("tarball download complete")
                    return True
                else:
                    _emit(f"tarball extract failed: {proc2.stderr[:100]}")
            else:
                _emit(f"tarball download failed (HTTP)")
        except (subprocess.TimeoutExpired, OSError) as e:
            _emit(f"tarball error: {e}")
        if os.path.isdir(dest):
            shutil.rmtree(dest, ignore_errors=True)

    return False


def _check_service_started(service_name: str, delay: float = 3.0) -> bool:
    """Check if a systemd service is still running after a short delay."""
    time.sleep(delay)
    try:
        r = subprocess.run(
            ["systemctl", "--user", "is-active", service_name],
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip() == "active"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def host_project(analysis: RepoAnalysis, port: int = 0,
                 verify: bool = True, work_dir: str | None = None,
                 on_status: callable = None,
                 sudo_password: str | None = None) -> HostResult:
    """Run the project and return service names and URLs.

    If *on_status* is given, it is called with each line of output
    so a TUI can show real-time progress.
    """
    def _emit(msg: str) -> None:
        if on_status:
            on_status(msg)

    # Clone first if deferred from quick analysis
    if analysis.clone_path is None:
        _emit("cloning repository...")
        try:
            ensure_cloned(analysis, work_dir=work_dir)
        except RuntimeError as e:
            result = HostResult()
            result.errors.append(str(e))
            return result
    if not analysis.clone_path:
        result = HostResult()
        result.errors.append("Cannot clone repository (check network connection)")
        return result

    # Run deep analysis if not already done (needed for strategy selection)
    if not analysis.deep_analysis:
        _deep_analyze_project(analysis)
        # Re-compute verdict with actual source analysis data
        analysis.can_host, analysis.reason = _can_host_verdict(analysis)

    if port == 0:
        port = find_free_port()

    project_dir = Path(analysis.clone_path)
    repo_url = analysis.url

    # Determine hosting strategy based on project type
    strategies = _strategy_priority(analysis)

    fn_map = {
        "Affine": lambda: _host_affine_systemd(project_dir, port, analysis.name, sudo_password=sudo_password),
        "OpenWebUI": lambda: _host_openwebui_systemd(project_dir, port, analysis.name, sudo_password=sudo_password),
        "Python": lambda: _host_python_systemd(project_dir, port, analysis.name),
        "Node.js": lambda: _host_node_systemd(project_dir, port, analysis.name),
        "Go": lambda: _host_go_systemd(project_dir, port, analysis.name),
        "Rust": lambda: _host_rust_systemd(project_dir, port, analysis.name),
        "Static": lambda: _host_static_systemd(project_dir, port, analysis.name),
    }

    strategy_list = [(name, fn_map[name]) for name in strategies if name in fn_map]

    if not strategy_list:
        raise RuntimeError("No hosting strategy available for this project")

    errors: list[str] = []
    for name, fn in strategy_list:
        _emit(f"trying {name} strategy...")
        strategy_result = HostResult()
        should_cleanup = False
        try:
            service_name = fn()
            strategy_result.service_names = [service_name]
            strategy_result.urls = [f"http://localhost:{port}"]
            # AFFiNE has an admin setup page in addition to the main URL
            if name == "Affine":
                strategy_result.urls.append(f"http://localhost:{port}/admin")
            if verify:
                strategy_result = verify_deployment(strategy_result)
            if strategy_result.healthy or (strategy_result.urls and strategy_result.service_names):
                _register_state(service_name, str(project_dir), repo_url)
                return strategy_result
            should_cleanup = True
            msg = strategy_result.errors[0] if strategy_result.errors else "unknown error"
            errors.append(f"[{name}] {msg}")
        except Exception as e:
            should_cleanup = True
            errors.append(f"[{name}] {e}")
        finally:
            if should_cleanup:
                _cleanup_strategy(strategy_result)

    raise RuntimeError("All strategies failed:\n" + "\n".join(errors))


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

    for candidate in ("app.py", "main.py"):
        f = project_dir / candidate
        if f.exists():
            try:
                content = f.read_text()
                if "FastAPI" in content:
                    return f"{candidate[:-3]}:app"
                if "Starlette" in content:
                    return f"{candidate[:-3]}:app"
            except OSError:
                pass

    for candidate in ("app.py", "main.py"):
        f = project_dir / candidate
        if f.exists():
            try:
                content = f.read_text()
                if "Flask" in content:
                    return f"{candidate[:-3]}:app"
            except OSError:
                pass

    return None


def _detect_python_entry(project_dir: Path) -> str | None:
    for entry in ("run.py", "server.py", "webapp.py", "wsgi.py", "asgi.py", "application.py"):
        f = project_dir / entry
        if f.exists():
            return entry[:-3]

    for pyfile in project_dir.iterdir():
        if pyfile.suffix == ".py" and pyfile.stem not in ("setup", "conf", "test", "tests", "conftest", "__init__"):
            try:
                content = pyfile.read_text()
                if any(x in content for x in ("app.run", "uvicorn.run", "gunicorn", "web.run", "make_server", "application.run")):
                    return pyfile.stem
            except OSError:
                pass

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


def _cleanup_cargo_build_artifacts(cargo_target_dir: str) -> None:
    """Remove cargo build artifacts to free disk space.

    After native modules are built, the intermediate build files (deps, .fingerprint,
    build) are no longer needed and can consume hundreds of MB.
    """
    dirs_to_clean = ["release/build", "release/deps", "release/.fingerprint",
                     "release/incremental", "debug/build", "debug/deps",
                     "debug/.fingerprint", "debug/incremental"]
    for d in dirs_to_clean:
        path = os.path.join(cargo_target_dir, d)
        if os.path.isdir(path):
            try:
                shutil.rmtree(path, ignore_errors=True)
            except OSError:
                pass


def _copy_or_symlink_tree(src: Path, dst: Path) -> None:
    """Copy directory tree, using symlinks for large files to save disk space.

    On tmpfs systems (like /tmp), disk space is precious. This function
    copies small files directly and creates symlinks for large files (>1MB).
    """
    dst.mkdir(parents=True, exist_ok=True)

    for item in src.rglob("*"):
        rel = item.relative_to(src)
        target = dst / rel

        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif item.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                # Use symlink for large files to save space
                if item.stat().st_size > 1_000_000:  # > 1MB
                    try:
                        target.symlink_to(item.resolve())
                    except OSError:
                        # Fallback to copy if symlink fails
                        shutil.copy2(item, target)
                else:
                    shutil.copy2(item, target)


def _host_affine_systemd(project_dir: Path, port: int, repo_url: str = "",
                         sudo_password: str | None = None) -> str:
    """Host AFFiNE self-hosted server using systemd.

    Full deployment pipeline:
    1. Check/start PostgreSQL and Redis
    2. Create database user and database
    3. Install pgvector extension
    4. Install yarn dependencies (skip-build)
    5. Build native modules (Rust via napi-rs)
    6. Build server
    7. Build frontend (web + admin)
    8. Copy frontend to server static directory
    9. Run Prisma migrations
    10. Create and start systemd service
    """
    import uuid
    service_name = f"ghost-affine-{uuid.uuid4().hex[:8]}"

    def _run(cmd: str, timeout: int = 300, check: bool = True) -> subprocess.CompletedProcess:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                           timeout=timeout, cwd=str(project_dir))
        if check and r.returncode != 0:
            raise RuntimeError(f"Command failed: {cmd}\n{r.stderr[:500]}")
        return r

    def _systemctl_user(action: str, unit: str) -> bool:
        try:
            r = subprocess.run(
                ["systemctl", "--user", action, unit],
                capture_output=True, timeout=30,
            )
            return r.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    # ── Find yarn 4 binary ──
    yarn4_bin = None
    for f in (project_dir / ".yarn" / "releases").iterdir():
        if f.suffix == ".cjs" and "yarn" in f.name:
            yarn4_bin = str(f)
            break
    if not yarn4_bin:
        raise RuntimeError("AFFiNE requires Yarn 4 (not found in .yarn/releases/)")
    yarn_cmd = f"node {yarn4_bin}"

    # ── Find Node.js 22+ (AFFiNE requires >=22.12.0 <23.0.0) ──
    node_bin = shutil.which("node") or ""
    if node_bin:
        try:
            r = subprocess.run([node_bin, "--version"], capture_output=True, text=True, timeout=5)
            ver = r.stdout.strip().lstrip("v")
            major = int(ver.split(".")[0]) if ver else 0
            if major < 22 or major >= 23:
                # Try to find Node.js 22 in common locations
                for candidate in [
                    os.path.expanduser("~/.local/share/nodejs/bin/node"),
                    "/usr/local/bin/node22",
                ]:
                    if os.path.isfile(candidate):
                        node_bin = candidate
                        break
        except (ValueError, subprocess.TimeoutExpired):
            pass

    node_dir = os.path.dirname(node_bin) if node_bin else ""
    rust_dir = os.path.expanduser("~/.rustup/toolchains")
    # Find a usable rustc toolchain
    rust_path = ""
    if os.path.isdir(rust_dir):
        for tc in sorted(os.listdir(rust_dir), reverse=True):
            cargo_path = os.path.join(rust_dir, tc, "bin", "cargo")
            if os.path.isfile(cargo_path):
                rust_path = os.path.join(rust_dir, tc, "bin")
                break

    env_path = ":".join(filter(None, [node_dir, rust_path, os.environ.get("PATH", "")]))

    # Use home directory for Cargo target (avoid /tmp which may be small tmpfs)
    cargo_target_dir = os.path.expanduser("~/.cache/affine-cargo-target")
    os.makedirs(cargo_target_dir, exist_ok=True)

    def _run_affine(cmd: str, timeout: int = 300, check: bool = True) -> subprocess.CompletedProcess:
        r = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=str(project_dir),
            env={**os.environ, "PATH": env_path, "CARGO_TARGET_DIR": cargo_target_dir},
        )
        if check and r.returncode != 0:
            raise RuntimeError(f"Command failed: {cmd}\n{r.stderr[:500]}")
        return r

    def _sudo(cmd: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
        """Run a command with sudo, piping password via stdin if available."""
        if sudo_password:
            return subprocess.run(
                ["sudo", "-S"] + cmd,
                input=sudo_password + "\n", capture_output=True, text=True, timeout=timeout,
            )
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

    # ══════════════════════════════════════════════
    # Step 1: Check/Start PostgreSQL
    # ══════════════════════════════════════════════
    pg_ready = False
    try:
        r = subprocess.run(
            ["pg_isready", "-h", "localhost"],
            capture_output=True, timeout=5,
        )
        pg_ready = r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    if not pg_ready:
        # Try to start PostgreSQL (system service, needs sudo)
        try:
            _sudo(["systemctl", "start", "postgresql"])
            time.sleep(2)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    # Check again
    try:
        r = subprocess.run(
            ["pg_isready", "-h", "localhost"],
            capture_output=True, timeout=5,
        )
        if r.returncode != 0:
            raise RuntimeError(
                "PostgreSQL is not running. Install and start it first:\n"
                "  sudo pacman -S postgresql\n"
                "  sudo postgresql-setup --initdb  # or: sudo -u postgres initdb -D /var/lib/postgres/data\n"
                "  sudo systemctl start postgresql"
            )
    except FileNotFoundError:
        raise RuntimeError("PostgreSQL not found. Install: sudo pacman -S postgresql")

    # ══════════════════════════════════════════════
    # Step 2: Check/Start Redis (Valkey)
    # ══════════════════════════════════════════════
    redis_ok = False
    try:
        r = subprocess.run(
            ["redis-cli", "ping"],
            capture_output=True, text=True, timeout=5,
        )
        redis_ok = "PONG" in r.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    if not redis_ok:
        # Try valkey-cli
        try:
            r = subprocess.run(
                ["valkey-cli", "ping"],
                capture_output=True, text=True, timeout=5,
            )
            redis_ok = "PONG" in r.stdout
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    if not redis_ok:
        # Try to start valkey/redis
        for svc in ("valkey", "redis"):
            try:
                _sudo(["systemctl", "start", svc])
                time.sleep(1)
                r = subprocess.run(
                    [f"{svc}-cli", "ping"],
                    capture_output=True, text=True, timeout=5,
                )
                if "PONG" in r.stdout:
                    redis_ok = True
                    break
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue

    if not redis_ok:
        raise RuntimeError(
            "Redis/Valkey is not running. Install and start it:\n"
            "  sudo pacman -S redis\n"
            "  sudo systemctl start redis"
        )

    # ══════════════════════════════════════════════
    # Step 3: Database setup
    # ══════════════════════════════════════════════
    db_user = "affine"
    db_pass = uuid.uuid4().hex  # random password
    db_name = "affine"

    # Check if database exists
    db_exists = False
    try:
        r = subprocess.run(
            ["psql", "-U", "postgres", "-h", "localhost", "-tAc",
             f"SELECT 1 FROM pg_roles WHERE rolname='{db_user}'"],
            capture_output=True, text=True, timeout=10,
        )
        db_exists = "1" in r.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    if not db_exists:
        # Create user and database — try password auth first (pg_hba.conf md5),
        # fall back to sudo if that fails
        try:
            r = subprocess.run(
                ["psql", "-U", "postgres", "-h", "localhost", "-c",
                 f"CREATE USER {db_user} WITH PASSWORD '{db_pass}';"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode != 0 and "password authentication failed" in r.stderr:
                # Password auth not configured — need sudo
                _sudo(["-u", "postgres", "psql", "-c",
                       f"CREATE USER {db_user} WITH PASSWORD '{db_pass}';"])
                _sudo(["-u", "postgres", "psql", "-c",
                       f"CREATE DATABASE {db_name} OWNER {db_user};"])
                _sudo(["-u", "postgres", "psql", "-c",
                       f"GRANT ALL PRIVILEGES ON DATABASE {db_name} TO {db_user};"])
            else:
                subprocess.run(
                    ["psql", "-U", "postgres", "-h", "localhost", "-c",
                     f"CREATE DATABASE {db_name} OWNER {db_user};"],
                    capture_output=True, timeout=10,
                )
                subprocess.run(
                    ["psql", "-U", "postgres", "-h", "localhost", "-c",
                     f"GRANT ALL PRIVILEGES ON DATABASE {db_name} TO {db_user};"],
                    capture_output=True, timeout=10,
                )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    # Check if pgvector is installed
    try:
        r = subprocess.run(
            ["psql", "-U", db_user, "-d", db_name, "-h", "localhost", "-tAc",
             "SELECT 1 FROM pg_extension WHERE extname='vector'"],
            capture_output=True, text=True, timeout=10,
        )
        if "1" not in r.stdout:
            # Try to create extension — password auth first, sudo fallback
            r2 = subprocess.run(
                ["psql", "-U", "postgres", "-h", "localhost", "-d", db_name, "-c",
                 "CREATE EXTENSION IF NOT EXISTS vector;"],
                capture_output=True, text=True, timeout=10,
            )
            if r2.returncode != 0:
                _sudo(["-u", "postgres", "psql", "-d", db_name, "-c",
                       "CREATE EXTENSION IF NOT EXISTS vector;"])
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # ══════════════════════════════════════════════
    # Step 4: Install dependencies
    # ══════════════════════════════════════════════
    _run_affine(f"{yarn_cmd} install --mode skip-build", timeout=600)

    # ══════════════════════════════════════════════
    # Step 5: Build native modules (Rust)
    # ══════════════════════════════════════════════
    _run_affine(f"{yarn_cmd} affine @affine/native build", timeout=900)
    _run_affine(f"{yarn_cmd} affine @affine/server-native build", timeout=900)

    # Create arch-suffixed symlinks for webpack resolution
    # Use symlinks instead of copies to save disk space on tmpfs
    native_dir = project_dir / "packages" / "backend" / "native"
    for f in native_dir.glob("*.node"):
        if ".arm" not in f.name and ".x64" not in f.name:
            base = f.stem
            for arch in ("arm64", "armv7", "x64"):
                target = native_dir / f"{base}.{arch}.node"
                if not target.exists():
                    try:
                        target.symlink_to(f.name)
                    except OSError:
                        # Fallback to copy if symlink fails (e.g., different filesystem)
                        shutil.copy2(f, target)

    # ══════════════════════════════════════════════
    # Step 6: Build server
    # ══════════════════════════════════════════════
    _run_affine(f"{yarn_cmd} affine @affine/server build", timeout=600)

    # Clean up cargo build artifacts to free disk space (especially important for tmpfs)
    _cleanup_cargo_build_artifacts(cargo_target_dir)

    # ══════════════════════════════════════════════
    # Step 7: Generate edgeless templates (required before web build)
    # ══════════════════════════════════════════════
    template_gen = project_dir / "packages" / "frontend" / "templates" / "build-edgeless.mjs"
    if template_gen.exists():
        _run_affine(f"{node_bin} {template_gen}", timeout=120, check=False)

    # ══════════════════════════════════════════════
    # Step 8: Build frontend (web + admin)
    # ══════════════════════════════════════════════
    _run_affine(f"{yarn_cmd} affine @affine/web build", timeout=600)
    # Admin must use local paths, not CDN — set PUBLIC_PATH for self-hosted
    _run_affine(f"PUBLIC_PATH=/admin/ {yarn_cmd} affine @affine/admin build", timeout=300)

    # ══════════════════════════════════════════════
    # Step 9: Copy frontend to server static dir
    # ══════════════════════════════════════════════
    server_dir = project_dir / "packages" / "backend" / "server"
    static_dir = server_dir / "static"
    web_dist = project_dir / "packages" / "frontend" / "apps" / "web" / "dist"
    admin_dist = project_dir / "packages" / "frontend" / "admin" / "dist"

    # Clean and recreate static dirs
    if static_dir.exists():
        shutil.rmtree(static_dir, ignore_errors=True)

    # Copy web frontend (use symlinks for large files to save disk space)
    if web_dist.exists():
        _copy_or_symlink_tree(web_dist, static_dir)
        # Also copy to mobile subdir (AFFiNE expects it)
        mobile_dir = static_dir / "mobile"
        _copy_or_symlink_tree(web_dist, mobile_dir)

    # Copy admin frontend
    if admin_dist.exists():
        admin_static = static_dir / "admin"
        _copy_or_symlink_tree(admin_dist, admin_static)

    # ══════════════════════════════════════════════
    # Step 10: Run Prisma migrations
    # ══════════════════════════════════════════════
    # Create .env for the server
    server_env = server_dir / ".env"
    server_env.write_text(
        f'DATABASE_URL="postgresql://{db_user}:{db_pass}@localhost:5432/{db_name}"\n'
        f"REDIS_SERVER_HOST=localhost\n"
        f"AFFINE_INDEXER_ENABLED=false\n"
    )

    # Generate Prisma client (must run from server dir where schema.prisma lives)
    _run_affine(
        f"cd {server_dir} && {yarn_cmd} prisma generate",
        timeout=120, check=False,
    )

    # Run migrations
    _run_affine(
        f"cd {server_dir} && {yarn_cmd} prisma migrate deploy",
        timeout=300, check=False,
    )

    # ══════════════════════════════════════════════
    # Step 11: Create systemd service
    # ══════════════════════════════════════════════
    _create_systemd_service(
        service_name=service_name,
        working_dir=str(server_dir),
        exec_start=f"{node_bin} dist/main.js",
        description="GhostProvider: AFFiNE",
        port=port,
        extra_env={
            "PATH": env_path,
            "DATABASE_URL": f"postgresql://{db_user}:{db_pass}@localhost:5432/{db_name}",
            "REDIS_SERVER_HOST": "localhost",
            "AFFINE_SERVER_PORT": str(port),
        },
    )

    # ══════════════════════════════════════════════
    # Step 12: Start the service
    # ══════════════════════════════════════════════
    _systemctl_user("start", service_name)

    return service_name


def _host_openwebui_systemd(project_dir: Path, port: int, repo_url: str = "",
                             sudo_password: str | None = None) -> str:
    """Host Open WebUI self-hosted AI interface using systemd.

    Full deployment pipeline:
    1. Find compatible Python (3.11-3.12, open-webui requires <3.13)
    2. Create Python venv and install backend dependencies
    3. Build frontend (SvelteKit) with npm
    4. Create .env with default configuration
    5. Create and start systemd service with uvicorn
    """
    import uuid
    service_name = f"ghost-openwebui-{uuid.uuid4().hex[:8]}"

    def _run(cmd: str, timeout: int = 300, check: bool = True,
             cwd: str | None = None) -> subprocess.CompletedProcess:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                           timeout=timeout, cwd=cwd or str(project_dir))
        if check and r.returncode != 0:
            raise RuntimeError(f"Command failed: {cmd}\n{r.stderr[:500]}")
        return r

    def _systemctl_user(action: str, unit: str) -> bool:
        try:
            r = subprocess.run(
                ["systemctl", "--user", action, unit],
                capture_output=True, timeout=30,
            )
            return r.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    # ══════════════════════════════════════════════
    # Step 1: Find compatible Python (3.11-3.12)
    # ══════════════════════════════════════════════
    # open-webui requires Python >=3.11, <3.13
    compatible_python = None
    for py_name in ("python3.11", "python3.12"):
        py_path = shutil.which(py_name)
        if py_path:
            try:
                r = subprocess.run([py_path, "--version"], capture_output=True, text=True, timeout=5)
                ver = r.stdout.strip()
                # Parse version: "Python 3.11.9" -> check major.minor
                m = re.search(r"(\d+)\.(\d+)", ver)
                if m:
                    major, minor = int(m.group(1)), int(m.group(2))
                    if major == 3 and minor in (11, 12):
                        compatible_python = py_path
                        break
            except (subprocess.TimeoutExpired, FileNotFoundError):
                continue

    if not compatible_python:
        raise RuntimeError(
            "Open WebUI requires Python 3.11 or 3.12, but none was found.\n"
            "Install Python 3.11: sudo pacman -S python311  (or equivalent)"
        )

    # ══════════════════════════════════════════════
    # Step 2: Create Python venv and install deps
    # ══════════════════════════════════════════════
    venv_dir = project_dir / ".venv"

    # Check if existing venv uses the right Python version
    venv_python = venv_dir / "bin" / "python"
    venv_ok = False
    if venv_python.exists():
        try:
            r = subprocess.run([str(venv_python), "--version"], capture_output=True, text=True, timeout=5)
            ver = r.stdout.strip()
            m = re.search(r"(\d+)\.(\d+)", ver)
            if m:
                major, minor = int(m.group(1)), int(m.group(2))
                if major == 3 and minor in (11, 12):
                    venv_ok = True
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    # Recreate venv if missing or wrong Python version
    if not venv_ok:
        if venv_dir.exists():
            shutil.rmtree(venv_dir, ignore_errors=True)
        r = subprocess.run(
            [compatible_python, "-m", "venv", str(venv_dir)],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode != 0:
            # Fallback: try --system-site-packages
            r = subprocess.run(
                [compatible_python, "-m", "venv", "--system-site-packages", str(venv_dir)],
                capture_output=True, text=True, timeout=60,
            )
            if r.returncode != 0:
                raise RuntimeError(f"Failed to create venv: {r.stderr[:300]}")

    pip = venv_dir / "bin" / "pip"
    python = venv_dir / "bin" / "python"

    if not pip.exists():
        # Fallback: try --system-site-packages
        subprocess.run(
            [compatible_python, "-m", "venv", "--system-site-packages", str(venv_dir)],
            capture_output=True, text=True, timeout=60,
        )

    # Upgrade pip
    try:
        subprocess.run(
            [str(pip), "install", "--upgrade", "pip"],
            capture_output=True, text=True, timeout=120,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Install backend dependencies from backend/requirements.txt
    backend_dir = project_dir / "backend"
    backend_req = backend_dir / "requirements.txt"
    installed_deps = False

    if backend_req.exists():
        r = subprocess.run(
            [str(pip), "install", "-r", str(backend_req)],
            capture_output=True, text=True, timeout=1200,
        )
        if r.returncode == 0:
            installed_deps = True
        else:
            # Try with --no-deps flag for partial installs
            r2 = subprocess.run(
                [str(pip), "install", "-r", str(backend_req), "--no-deps"],
                capture_output=True, text=True, timeout=600,
            )
            if r2.returncode == 0:
                installed_deps = True

    # Fallback: install from root pyproject.toml (hatchling build)
    if not installed_deps:
        root_pyproject = project_dir / "pyproject.toml"
        if root_pyproject.exists():
            r = subprocess.run(
                [str(pip), "install", "-e", "."],
                capture_output=True, text=True, timeout=1200,
                cwd=str(project_dir),
            )
            if r.returncode != 0:
                # Try without editable mode
                subprocess.run(
                    [str(pip), "install", "."],
                    capture_output=True, text=True, timeout=1200,
                    cwd=str(project_dir),
                )

    # Verify critical packages are installed
    critical_missing = []
    for pkg in ("uvicorn", "fastapi"):
        try:
            subprocess.run(
                [str(python), "-c", f"import {pkg}"],
                capture_output=True, timeout=10,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            critical_missing.append(pkg)

    if critical_missing:
        # Last resort: install critical packages directly
        subprocess.run(
            [str(pip), "install"] + critical_missing,
            capture_output=True, text=True, timeout=300,
        )

    # ══════════════════════════════════════════════
    # Step 3: Find compatible Node.js (18-22)
    # ══════════════════════════════════════════════
    # open-webui requires Node.js >=18.13.0, <=22.x.x
    compatible_node = None
    compatible_npm = None
    node_dir = None

    # Check common locations for compatible Node.js
    for candidate in (
        shutil.which("node22"), shutil.which("node20"), shutil.which("node18"),
        os.path.expanduser("~/.local/share/nodejs/bin/node"),
        shutil.which("node"),
    ):
        if not candidate or not os.path.isfile(candidate):
            continue
        try:
            r = subprocess.run([candidate, "--version"], capture_output=True, text=True, timeout=5)
            ver = r.stdout.strip()
            m = re.search(r"v(\d+)\.", ver)
            if m:
                major = int(m.group(1))
                if 18 <= major <= 22:
                    compatible_node = candidate
                    node_dir = os.path.dirname(candidate)
                    # Find npm in same directory
                    npm_candidate = os.path.join(node_dir, "npm")
                    if os.path.isfile(npm_candidate):
                        compatible_npm = npm_candidate
                    else:
                        compatible_npm = shutil.which("npm")
                    break
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue

    if not compatible_node:
        raise RuntimeError(
            "Open WebUI requires Node.js 18-22, but none was found.\n"
            "Install Node.js 22: https://nodejs.org/ or use nvm/fnm"
        )

    # ══════════════════════════════════════════════
    # Step 4: Build frontend (SvelteKit)
    # ══════════════════════════════════════════════
    frontend_built = False

    # Check if frontend is already built
    build_dir = project_dir / "build"
    if (build_dir / "index.html").exists():
        frontend_built = True

    if not frontend_built:
        pkg_json = project_dir / "package.json"
        if pkg_json.exists():
            # Use compatible Node.js/npm
            npm_cmd = compatible_npm or "npm"

            # Build env with correct PATH so npm finds the right node
            npm_env = os.environ.copy()
            if node_dir:
                npm_env["PATH"] = f"{node_dir}:{npm_env.get('PATH', '')}"

            # Install frontend dependencies
            # Try normal install first, fall back to --ignore-scripts if sharp fails
            r = subprocess.run(
                [compatible_npm, "install", "--legacy-peer-deps"],
                capture_output=True, text=True,
                timeout=600, cwd=str(project_dir),
                env=npm_env,
            )
            if r.returncode != 0:
                # sharp build likely failed — retry with --ignore-scripts
                r = subprocess.run(
                    [compatible_npm, "install", "--legacy-peer-deps", "--ignore-scripts"],
                    capture_output=True, text=True,
                    timeout=600, cwd=str(project_dir),
                    env=npm_env,
                )
                # Install missing peer deps that --ignore-scripts may have skipped
                subprocess.run(
                    [compatible_npm, "install", "@internationalized/date", "--legacy-peer-deps"],
                    capture_output=True, text=True,
                    timeout=120, cwd=str(project_dir),
                    env=npm_env,
                )

            # Build frontend — run vite build directly (skip pyodide:fetch which needs network)
            vite_bin = project_dir / "node_modules" / ".bin" / "vite"
            if vite_bin.exists():
                r = subprocess.run(
                    [str(vite_bin), "build"], capture_output=True, text=True,
                    timeout=600, cwd=str(project_dir),
                    env=npm_env,
                )
            else:
                r = subprocess.run(
                    [compatible_npm, "exec", "vite", "build"], capture_output=True, text=True,
                    timeout=600, cwd=str(project_dir),
                    env=npm_env,
                )
            if r.returncode == 0 and (build_dir / "index.html").exists():
                frontend_built = True

    if not frontend_built:
        # Check if there's a pre-built frontend in backend/open_webui/frontend
        backend_frontend = backend_dir / "open_webui" / "frontend"
        if (backend_frontend / "index.html").exists():
            frontend_built = True

    if not frontend_built:
        raise RuntimeError(
            "Failed to build Open WebUI frontend.\n"
            "Ensure Node.js and npm are installed, then try again."
        )

    # ══════════════════════════════════════════════
    # Step 5: Create .env with default configuration
    # ══════════════════════════════════════════════
    # open-webui looks for .env at project root (BASE_DIR), not in backend/
    env_file = project_dir / ".env"
    import secrets as _secrets
    secret_key = _secrets.token_hex(32)

    if not env_file.exists():
        # Check for .env.example at root
        env_example = project_dir / ".env.example"
        if env_example.exists():
            content = env_example.read_text()
            # Ensure WEBUI_SECRET_KEY is present
            if "WEBUI_SECRET_KEY" not in content:
                content = f"WEBUI_SECRET_KEY={secret_key}\n{content}"
            else:
                content = re.sub(
                    r"WEBUI_SECRET_KEY\s*=\s*['\"]?.*?['\"]?",
                    f"WEBUI_SECRET_KEY={secret_key}",
                    content,
                )
            # Set default DATA_DIR
            data_dir = project_dir / "backend" / "data"
            data_dir.mkdir(exist_ok=True)
            if "DATA_DIR" not in content:
                content = f"{content}\nDATA_DIR={data_dir}\n"
            env_file.write_text(content)
        else:
            # Create minimal .env
            data_dir = project_dir / "backend" / "data"
            data_dir.mkdir(exist_ok=True)
            env_file.write_text(
                f"WEBUI_SECRET_KEY={secret_key}\n"
                f"DATA_DIR={data_dir}\n"
                f"OLLAMA_BASE_URL=http://localhost:11434\n"
            )
    else:
        # Ensure existing .env has WEBUI_SECRET_KEY
        content = env_file.read_text()
        if "WEBUI_SECRET_KEY" not in content:
            content = f"WEBUI_SECRET_KEY={secret_key}\n{content}"
            env_file.write_text(content)

    # ══════════════════════════════════════════════
    # Step 5: Create systemd service
    # ══════════════════════════════════════════════
    backend_main = backend_dir / "open_webui" / "main.py"
    if not backend_main.exists():
        raise RuntimeError("Cannot find Open WebUI backend/main.py")

    working_dir = str(backend_dir)
    exec_module = "open_webui.main:app"

    exec_start = (
        f"{python} -m uvicorn {exec_module} "
        f"--host 127.0.0.1 --port {port} "
        f"--forwarded-allow-ips='*'"
    )

    # Build PYTHONPATH: backend dir + project root (for open_webui package)
    pythonpath = f"{backend_dir}:{project_dir}"

    # Build PATH: include Node.js dir for any runtime needs
    env_path = os.environ.get("PATH", "")
    if node_dir:
        env_path = f"{node_dir}:{env_path}"

    _create_systemd_service(
        service_name=service_name,
        working_dir=working_dir,
        exec_start=exec_start,
        description="GhostProvider: Open WebUI",
        port=port,
        extra_env={
            "PYTHONPATH": pythonpath,
            "ENV": "prod",
            "PATH": env_path,
        },
    )

    # ══════════════════════════════════════════════
    # Step 6: Start the service
    # ══════════════════════════════════════════════
    _systemctl_user("start", service_name)

    # Wait for startup
    time.sleep(3)

    # Check if service is still running
    try:
        r = subprocess.run(
            ["systemctl", "--user", "is-active", service_name],
            capture_output=True, text=True, timeout=5,
        )
        if r.stdout.strip() != "active":
            # Get logs for debugging
            logs_r = subprocess.run(
                ["journalctl", "-u", service_name, "-n", "30", "--no-pager"],
                capture_output=True, text=True, timeout=10,
            )
            logs = logs_r.stdout if logs_r.returncode == 0 else ""
            raise RuntimeError(
                f"Open WebUI service failed to start. Logs:\n{logs[:500]}"
            )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return service_name


def _host_python_systemd(project_dir: Path, port: int, repo_url: str = "") -> str:
    """Host a Python project using systemd.

    Creates an isolated venv, installs deps there, detects entry point,
    and binds to 127.0.0.1 for privacy.

    Falls back to PYTHONPATH-based execution when pip install fails
    (common with projects that have build-time deps like SearXNG).
    """
    import uuid
    service_name = f"ghost-py-{uuid.uuid4().hex[:8]}"

    has_pyproject = (project_dir / "pyproject.toml").exists()
    has_setup = (project_dir / "setup.py").exists()
    has_manage = (project_dir / "manage.py").exists()

    # ── 1. Create venv and install deps ──
    venv_dir = project_dir / ".venv"
    python = _ensure_venv(venv_dir, project_dir)
    python_bin = str(python)

    # Try to install the package — track if it succeeds
    package_installed = False
    if has_pyproject or has_setup:
        pip = venv_dir / "bin" / "pip"
        if pip.exists():
            try:
                r = subprocess.run(
                    [str(pip), "install", "-e", "."],
                    capture_output=True, text=True, timeout=600,
                    cwd=str(project_dir),
                )
                package_installed = r.returncode == 0
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass

    # ── 2. Auto-create .env from .env.example ──
    _auto_env(project_dir)

    # ── 3. Detect entry point and build command ──
    wsgi_module = _detect_wsgi_module(project_dir)
    py_entry = _detect_python_entry(project_dir)

    # SearXNG / Flask / FastAPI auto-config
    _prepare_searxng_config(project_dir, port)

    # Extra env vars for systemd (PYTHONPATH when package isn't installed)
    extra_env: dict[str, str] = {}
    if not package_installed:
        extra_env["PYTHONPATH"] = str(project_dir)

    if has_manage and wsgi_module:
        cmd = (
            f"{python_bin} -m gunicorn --bind 127.0.0.1:{port} {wsgi_module} "
            f"|| {python_bin} -m uvicorn --host 127.0.0.1 --port {port} {wsgi_module}"
        )
    elif has_manage:
        cmd = f"{python_bin} manage.py runserver 0.0.0.0:{port}"
    elif wsgi_module:
        cmd = (
            f"{python_bin} -m gunicorn --bind 127.0.0.1:{port} {wsgi_module} "
            f"|| {python_bin} -m uvicorn --host 127.0.0.1 --port {port} {wsgi_module}"
        )
    elif py_entry:
        if "." in py_entry:
            cmd = f"{python_bin} -m {py_entry}"
        else:
            cmd = f"{python_bin} {py_entry}.py"
    else:
        # Last resort: generic HTTP server
        cmd = f"{python_bin} -m http.server {port} --bind 127.0.0.1"

    # ── 4. Create systemd service ──
    _create_systemd_service(
        service_name=service_name,
        working_dir=str(project_dir),
        exec_start=cmd,
        description=f"GhostProvider: {repo_url}",
        port=port,
        extra_env=extra_env,
    )

    # ── 5. Start ──
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

    return service_name


def _ensure_venv(venv_dir: Path, project_dir: Path) -> Path:
    """Create a venv and install project deps into it. Returns path to python.

    Falls back to --system-site-packages when the standard venv lacks
    critical packages (e.g. setuptools on Python 3.14).
    """
    if not venv_dir.exists():
        # Try standard venv first
        try:
            subprocess.run(
                ["python3", "-m", "venv", str(venv_dir)],
                capture_output=True, timeout=60,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        # If pip is missing (Python 3.14+), retry with --system-site-packages
        if not (venv_dir / "bin" / "pip").exists():
            shutil.rmtree(venv_dir, ignore_errors=True)
            try:
                subprocess.run(
                    ["python3", "-m", "venv", "--system-site-packages", str(venv_dir)],
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
                    capture_output=True, text=True, timeout=600,
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


def _find_affine_web_dist(project_dir: Path) -> str | None:
    """Find AFFiNE web app dist directory in a monorepo structure."""
    # Common AFFiNE layout: packages/frontend/apps/web/dist/
    candidates = [
        project_dir / "packages" / "frontend" / "apps" / "web" / "dist",
        project_dir / "packages" / "frontend" / "web" / "dist",
        project_dir / "apps" / "web" / "dist",
    ]
    for c in candidates:
        if c.is_dir() and (c / "index.html").exists():
            return str(c)
    # Fallback: search for any dist/ with index.html in subdirectories
    for dist_dir in project_dir.rglob("dist"):
        if dist_dir.is_dir() and (dist_dir / "index.html").exists():
            return str(dist_dir)
    return None


def _host_node_systemd(project_dir: Path, port: int, repo_url: str = "") -> str:
    """Host a Node.js project using systemd.

    Handles monorepos (yarn workspaces, npm workspaces), SvelteKit,
    Next.js, Yarn 4 projects (e.g. AFFiNE), and generic Node apps.
    Detects package manager (npm/yarn/pnpm/bun) from lock files.
    """
    import uuid
    service_name = f"ghost-js-{uuid.uuid4().hex[:8]}"

    pkg = _read_package_json(project_dir)

    # ── Detect package manager ──
    pm = _detect_node_pm(project_dir)
    run_cmd = pm["run"]
    install_cmd = pm["install"]
    serve_cmd = pm["serve"]

    # ── Detect Yarn 4 (bundled in .yarn/releases/) ──
    is_yarn4 = (project_dir / ".yarn" / "releases").is_dir()
    yarn4_bin = None
    if is_yarn4:
        for f in (project_dir / ".yarn" / "releases").iterdir():
            if f.suffix == ".cjs" and "yarn" in f.name:
                yarn4_bin = str(f)
                break
    if yarn4_bin:
        run_cmd = f"node {yarn4_bin}"
        install_cmd = f"node {yarn4_bin} install --no-immutable --mode skip-build"

    # ── Detect monorepo ──
    workspaces = _detect_workspaces(project_dir, pkg)
    if workspaces:
        # In a monorepo, try to find the web app package
        app_dir = _find_webapp_in_monorepo(project_dir, workspaces)
        if app_dir and app_dir != project_dir:
            project_dir = app_dir
            pkg = _read_package_json(project_dir)

    # ── Detect Electron ──
    all_deps = {}
    if pkg:
        all_deps.update(pkg.get("dependencies", {}))
        all_deps.update(pkg.get("devDependencies", {}))
    is_electron = "electron" in all_deps

    scripts = (pkg or {}).get("scripts", {})
    has_build = "build" in scripts
    has_start = "start" in scripts
    has_dev = "dev" in scripts
    has_preview = "preview" in scripts

    # ── Detect SvelteKit ──
    is_sveltekit = (project_dir / "svelte.config.js").exists() or (
        project_dir / "svelte.config.ts").exists()
    if pkg:
        is_sveltekit = is_sveltekit or "@sveltejs/kit" in all_deps

    # ── Detect AFFiNE (Yarn 4 monorepo with custom build) ──
    is_affine = is_yarn4 and "affine" in str(project_dir).lower()
    # Also detect via package.json name
    if pkg and pkg.get("name", "") == "@affine/monorepo":
        is_affine = True
    if pkg and pkg.get("name", "").startswith("@affine/"):
        is_affine = True

    # ── Auto-create .env ──
    _auto_env(project_dir)

    # ── Build ──
    build_layer = ""
    serve_full = ""
    # Track if we need to serve from a non-standard output dir
    serve_from_subdir = None

    if is_affine and yarn4_bin:
        # AFFiNE: generate templates, then bundle the web package
        # The web app output goes to packages/frontend/apps/web/dist/
        templates_dir = project_dir / "packages" / "frontend" / "templates"
        template_gen = templates_dir / "build-edgeless.mjs"
        build_layer = (
            f"/bin/sh -c 'cd {templates_dir} && node build-edgeless.mjs 2>/dev/null; "
            f"cd {project_dir} && {run_cmd} affine bundle -p web'"
        )
        # Find the web app dist directory
        affine_web_dist = _find_affine_web_dist(project_dir)
        if affine_web_dist:
            serve_full = (
                f"/bin/sh -c '{serve_cmd} -s {affine_web_dist} -l {port} "
                f"|| python3 -m http.server {port} --directory {affine_web_dist}'"
            )
        else:
            serve_full = f"{serve_cmd} -s . -l {port}"
    elif is_sveltekit:
        build_layer = f"{run_cmd} run build"
        serve_full = (
            f"/bin/sh -c '{serve_cmd} -s dist/renderer -l {port} "
            f"|| {serve_cmd} -s dist -l {port} "
            f"|| {serve_cmd} -s . -l {port}'"
        )
    elif has_build and has_start:
        build_layer = f"{run_cmd} run build"
        serve_full = f"{run_cmd} run start"
    elif has_build:
        build_layer = f"{run_cmd} run build"
        # After build, check common output dirs
        serve_full = (
            f"/bin/sh -c '{serve_cmd} -s build -l {port} "
            f"|| {serve_cmd} -s dist -l {port} "
            f"|| {serve_cmd} -s public -l {port} "
            f"|| python3 -m http.server {port} --directory build "
            f"|| python3 -m http.server {port} --directory dist "
            f"|| python3 -m http.server {port} --directory public'"
        )
    elif has_preview:
        build_layer = ""
        serve_full = f"{run_cmd} run preview --host 127.0.0.1 --port {port}"
    elif has_dev:
        build_layer = ""
        serve_full = f"{run_cmd} run dev --host 127.0.0.1 --port {port}"
    elif has_start:
        build_layer = ""
        serve_full = f"{run_cmd} run start"
    else:
        build_layer = ""
        serve_full = f"{serve_cmd} -s . -l {port}"

    # ── Install deps ──
    _install_project_deps(project_dir)

    # ── Build ──
    if build_layer:
        r = subprocess.run(
            build_layer, shell=True, capture_output=True, text=True,
            timeout=900, cwd=str(project_dir),
        )
        # Retry without paraglide compile if it fails
        if r.returncode != 0 and "paraglide" in r.stderr.lower():
            vite_cmd = build_layer.replace("paraglide-js compile && ", "").replace(
                "paraglide-js compile &&", "")
            if vite_cmd != build_layer:
                r = subprocess.run(
                    vite_cmd, shell=True, capture_output=True, text=True,
                    timeout=900, cwd=str(project_dir),
                )
        if r.returncode != 0:
            raise RuntimeError(f"Build failed: {r.stderr[:300]}")

    # ── Create systemd service ──
    _create_systemd_service(
        service_name=service_name,
        working_dir=str(project_dir),
        exec_start=serve_full,
        description=f"GhostProvider: {repo_url}",
        port=port,
    )

    # ── Start ──
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

    return service_name


def _detect_node_pm(project_dir: Path) -> dict[str, str]:
    """Detect Node.js package manager from lock files.

    Returns full paths to executables so systemd services can find them.
    """
    has_bun = (project_dir / "bun.lock").exists() or (project_dir / "bun.lockb").exists()
    has_pnpm = (project_dir / "pnpm-lock.yaml").exists()
    has_yarn = (project_dir / "yarn.lock").exists()
    # Yarn 4: bundled binary in .yarn/releases/
    is_yarn4 = has_yarn and (project_dir / ".yarn" / "releases").is_dir()

    if has_bun:
        bun = shutil.which("bun")
        if bun:
            return {"run": bun, "install": f"{bun} install", "serve": f"{bun}x serve"}
    if has_pnpm:
        pnpm = shutil.which("pnpm")
        if pnpm:
            return {"run": pnpm, "install": f"{pnpm} install", "serve": f"{pnpm} dlx serve"}
    if is_yarn4:
        # Yarn 4: use bundled binary via node
        for f in (project_dir / ".yarn" / "releases").iterdir():
            if f.suffix == ".cjs" and "yarn" in f.name:
                return {"run": f"node {f}", "install": f"node {f} install --no-immutable --mode skip-build", "serve": "npx serve"}
    if has_yarn:
        yarn = shutil.which("yarn")
        if yarn:
            return {"run": yarn, "install": f"{yarn} install", "serve": "npx serve"}
    npm = shutil.which("npm") or "npm"
    npx = shutil.which("npx") or "npx"
    return {"run": npm, "install": f"{npm} install", "serve": f"{npx} serve"}


def _detect_workspaces(project_dir: Path, pkg: dict | None) -> list[str] | None:
    """Detect monorepo workspaces from package.json."""
    if not pkg:
        return None
    ws = pkg.get("workspaces")
    if isinstance(ws, list):
        return ws
    if isinstance(ws, dict):
        return ws.get("packages", [])
    return None


def _find_webapp_in_monorepo(project_dir: Path, workspaces: list[str]) -> Path | None:
    """In a monorepo, find the package most likely to be the web app."""
    import glob as _glob

    candidates: list[Path] = []
    for pattern in workspaces:
        matches = _glob.glob(str(project_dir / pattern), recursive=False)
        for m in matches:
            p = Path(m)
            if not p.is_dir():
                continue
            # Check if this package has a web app
            sub_pkg = _read_package_json(p)
            if not sub_pkg:
                continue
            sub_deps = {}
            sub_deps.update(sub_pkg.get("dependencies", {}))
            sub_deps.update(sub_pkg.get("devDependencies", {}))
            sub_scripts = sub_pkg.get("scripts", {})
            # Prefer packages with web framework deps or web scripts
            score = 0
            web_deps = {"react", "vue", "svelte", "@sveltejs/kit", "next", "nuxt",
                         "angular", "@angular/core", "@nestjs/core", "express", "fastify"}
            if sub_deps.keys() & web_deps:
                score += 10
            if any(s in sub_scripts for s in ("dev", "build", "start")):
                score += 5
            if (p / "src").is_dir():
                score += 3
            candidates.append((score, p))

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]
    return None


def _host_static_systemd(project_dir: Path, port: int, repo_url: str = "") -> str:
    """Host a static site using systemd and Python HTTP server."""
    import uuid
    service_name = f"ghost-static-{uuid.uuid4().hex[:8]}"

    # Use Python's built-in HTTP server for static files
    exec_start = f"python3 -m http.server {port} --directory {project_dir}"

    # Create systemd service
    _create_systemd_service(
        service_name=service_name,
        working_dir=str(project_dir),
        exec_start=exec_start,
        description=f"GhostProvider: {repo_url}",
        port=port,
    )

    # Start the service
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
        logs = _get_service_logs(service_name, 10)
        raise RuntimeError(f"Service crashed immediately after start: {logs[:200]}")

    return service_name


def _host_go_systemd(project_dir: Path, port: int, repo_url: str = "") -> str:
    """Host a Go project using systemd.

    Scans for existing binaries, tries multiple build targets,
    falls back to `go run` if compilation fails.
    """
    import uuid
    service_name = f"ghost-go-{uuid.uuid4().hex[:8]}"

    output_bin = str(project_dir / "ghost-server")

    # ── 1. Check for existing compiled binary ──
    binary_path = _find_existing_go_binary(project_dir)

    # ── 2. Build if no binary found ──
    if not binary_path:
        build_targets = _detect_go_build_targets(project_dir)
        proxies = ["https://proxy.golang.org,direct", "direct"]

        env = {**os.environ, "GOPROXY": ",".join(proxies)}

        for target in build_targets:
            cmd = ["go", "build", "-o", output_bin]
            if target != ".":
                cmd.append(target)
            try:
                r = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=600,
                    cwd=str(project_dir), env=env,
                )
                if r.returncode == 0 and os.path.isfile(output_bin):
                    binary_path = output_bin
                    break
            except (subprocess.TimeoutExpired, FileNotFoundError):
                continue

    # ── 3. Determine exec_start ──
    if binary_path:
        # Try common port flag formats
        exec_start = f"{binary_path} --port {port}"
    else:
        # Fallback: go run (slower startup, but works)
        target = _detect_go_build_targets(project_dir)[0] if _detect_go_build_targets(project_dir) else "."
        exec_start = f"go run {target} --port {port}"

    # ── 4. Create and start ──
    _create_systemd_service(
        service_name=service_name,
        working_dir=str(project_dir),
        exec_start=exec_start,
        description=f"GhostProvider: {repo_url}",
        port=port,
    )

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

    return service_name


def _find_existing_go_binary(project_dir: Path) -> str | None:
    """Find a pre-compiled Go binary in the project."""
    common_names = ("server", "app", "main", project_dir.name, "ghost-server")
    search_dirs = [project_dir, project_dir / "bin", project_dir / "target", project_dir / "cmd"]

    for d in search_dirs:
        if not d.is_dir():
            continue
        for name in common_names:
            p = d / name
            if p.is_file() and os.access(p, os.X_OK):
                return str(p)
    return None


def _detect_go_build_targets(project_dir: Path) -> list[str]:
    """Detect possible Go build targets (cmd directories, main.go locations)."""
    targets: list[str] = []

    # Check cmd/ directory (standard Go convention)
    cmd_dir = project_dir / "cmd"
    if cmd_dir.is_dir():
        for entry in cmd_dir.iterdir():
            if entry.is_dir() and (entry / "main.go").exists():
                targets.append(f"./cmd/{entry.name}")

    # Check for main.go in root
    if (project_dir / "main.go").exists():
        targets.append(".")

    # Check for any .go file with func main()
    if not targets:
        for gofile in project_dir.rglob("*.go"):
            if gofile.stat().st_size > 50000:
                continue
            try:
                if "func main()" in gofile.read_text(errors="replace"):
                    # Relative path from project_dir
                    rel = gofile.relative_to(project_dir)
                    targets.append(f"./{rel.parent}" if rel.parent != Path(".") else ".")
                    break
            except OSError:
                continue

    if not targets:
        targets.append(".")

    return targets


def _detect_rust_binary(project_dir: Path) -> str | None:
    """Extract binary/package name from Cargo.toml."""
    cargo_toml = project_dir / "Cargo.toml"
    if not cargo_toml.exists():
        return None
    try:
        content = cargo_toml.read_text()
        m = re.search(r'\[\[bin\]\][^[]*name\s*=\s*"(.+?)"', content, re.DOTALL)
        if m:
            return m.group(1)
        m = re.search(r'\[package\][^[]*name\s*=\s*"(.+?)"', content, re.DOTALL)
        if m:
            return m.group(1)
    except OSError:
        pass
    return None


def _host_rust_systemd(project_dir: Path, port: int, repo_url: str = "") -> str:
    """Host a Rust project using systemd."""
    import uuid
    service_name = f"ghost-rust-{uuid.uuid4().hex[:8]}"

    bin_name = _detect_rust_binary(project_dir) or "app"

    # Build the Rust binary
    try:
        r = subprocess.run(
            ["cargo", "build", "--release", "--bin", bin_name],
            capture_output=True, text=True, timeout=600,
            cwd=str(project_dir),
        )
        if r.returncode != 0:
            # Try building without specifying binary
            r = subprocess.run(
                ["cargo", "build", "--release"],
                capture_output=True, text=True, timeout=600,
                cwd=str(project_dir),
            )
            if r.returncode != 0:
                raise RuntimeError(f"Cargo build failed: {r.stderr}")
    except FileNotFoundError:
        raise RuntimeError("Cargo/Rust compiler not found")

    binary_path = project_dir / "target" / "release" / bin_name
    if not binary_path.exists():
        # Try the project name
        binary_path = project_dir / "target" / "release" / project_dir.name

    if not binary_path.exists():
        raise RuntimeError(f"Built binary not found at {binary_path}")

    # Create systemd service
    _create_systemd_service(
        service_name=service_name,
        working_dir=str(project_dir),
        exec_start=f"{binary_path} --port {port}",
        description=f"GhostProvider: {repo_url}",
        port=port,
    )

    # Start the service
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
        logs = _get_service_logs(service_name, 10)
        raise RuntimeError(f"Service crashed immediately after start: {logs[:200]}")

    return service_name


def _detect_node_port(pkg: dict | None) -> int:
    """Try to detect the port a Node.js app listens on from package.json scripts."""
    if not pkg:
        return 3000
    scripts = pkg.get("scripts", {})
    for script_name in ("start", "dev", "serve"):
        script = scripts.get(script_name, "")
        if not script:
            continue
        m = re.search(r'(?:-p|--port)(?:\s+|=|:)\s*(\d+)', script)
        if m:
            return int(m.group(1))
        m = re.search(r'(?:PORT)=(\d+)', script)
        if m:
            return int(m.group(1))
    return 3000


def _read_package_json(project_dir: Path) -> dict | None:
    pkg_file = project_dir / "package.json"
    if not pkg_file.exists():
        return None
    try:
        return json.loads(pkg_file.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _create_systemd_service(service_name: str, working_dir: str,
                            exec_start: str, description: str = "",
                            port: int = 0,
                            extra_env: dict[str, str] | None = None) -> None:
    """Create a systemd service unit file with security hardening.

    Privacy measures:
    - PrivateTmp=yes: isolated /tmp (no leaking between services)
    - NoNewPrivileges=yes: prevents privilege escalation
    - ProtectSystem=strict: read-only filesystem except working dir
    - ProtectHome=yes: no access to home directory
    - RestrictNamespaces=yes: no namespace creation
    - RestrictSUIDSGID=yes: no SUID/SGID bits
    - LockPersonality=yes: prevents personality() syscalls
    """
    user_dir = os.path.expanduser("~/.config/systemd/user")
    os.makedirs(user_dir, exist_ok=True)

    # Build environment lines
    env_lines = ""
    if extra_env:
        for k, v in extra_env.items():
            env_lines += f'Environment="{k}={v}"\n'

    unit_content = f"""[Unit]
Description={description or service_name}
After=network.target

[Service]
Type=simple
WorkingDirectory={working_dir}
ExecStart={exec_start}
Restart=on-failure
RestartSec=5
{env_lines}
# ── Privacy & Security Hardening ──
NoNewPrivileges=yes
ProtectHome=read-only
ProtectSystem=read-only
ReadWritePaths={working_dir}

[Install]
WantedBy=default.target
"""

    unit_path = os.path.join(user_dir, f"{service_name}.service")
    with open(unit_path, "w") as f:
        f.write(unit_content)

    # Reload systemd daemon
    try:
        subprocess.run(
            ["systemctl", "--user", "daemon-reload"],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Enable the service
    try:
        subprocess.run(
            ["systemctl", "--user", "enable", service_name],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass


def _install_project_deps(project_dir: Path, on_status: Callable[[str], None] | None = None) -> None:
    """Install project dependencies based on project type.

    Creates venvs for Python projects (never installs system-wide).
    Detects package managers from lock files.
    """
    def _emit(msg: str) -> None:
        if on_status:
            on_status(msg)

    # ── Python: always use venv ──
    if (project_dir / "requirements.txt").exists() or (project_dir / "pyproject.toml").exists() or (project_dir / "setup.py").exists():
        venv_dir = project_dir / ".venv"
        if not venv_dir.exists():
            _emit("creating Python venv...")
            try:
                subprocess.run(
                    ["python3", "-m", "venv", str(venv_dir)],
                    capture_output=True, timeout=60,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError):
                _emit("venv creation failed, trying system pip...")

            # If pip is missing (Python 3.14+), retry with --system-site-packages
            if not (venv_dir / "bin" / "pip").exists():
                shutil.rmtree(venv_dir, ignore_errors=True)
                try:
                    subprocess.run(
                        ["python3", "-m", "venv", "--system-site-packages", str(venv_dir)],
                        capture_output=True, timeout=60,
                    )
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    pass

        pip = venv_dir / "bin" / "pip"
        if pip.exists():
            # Upgrade pip
            try:
                subprocess.run([str(pip), "install", "--upgrade", "pip"],
                               capture_output=True, timeout=120)
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass

            req = project_dir / "requirements.txt"
            if req.exists():
                _emit("installing Python dependencies...")
                try:
                    subprocess.run(
                        [str(pip), "install", "-r", str(req)],
                        capture_output=True, text=True, timeout=600,
                    )
                    _emit("Python dependencies installed")
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    _emit("Python dependency install failed or timed out")

            # Install the package itself
            if (project_dir / "pyproject.toml").exists() or (project_dir / "setup.py").exists():
                try:
                    subprocess.run(
                        [str(pip), "install", "-e", "."],
                        capture_output=True, text=True, timeout=600,
                        cwd=str(project_dir),
                    )
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    pass

    # ── Node.js: detect package manager from lock files ──
    if (project_dir / "package.json").exists():
        # Detect Yarn 4 (bundled in .yarn/releases/)
        is_yarn4 = (project_dir / ".yarn" / "releases").is_dir()
        if is_yarn4:
            yarn4_bin = None
            for f in (project_dir / ".yarn" / "releases").iterdir():
                if f.suffix == ".cjs" and "yarn" in f.name:
                    yarn4_bin = str(f)
                    break
            if yarn4_bin:
                _emit("installing Node.js dependencies (yarn 4)...")
                try:
                    subprocess.run(
                        ["node", yarn4_bin, "install", "--no-immutable", "--mode", "skip-build"],
                        capture_output=True, text=True, timeout=900,
                        cwd=str(project_dir),
                    )
                    _emit("Node.js dependencies installed")
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    _emit("Node.js dependency install failed or timed out")
            else:
                _emit("yarn 4 binary not found, falling back to npm...")
                pm = _detect_node_pm(project_dir)
                try:
                    subprocess.run(
                        pm["install"].split(),
                        capture_output=True, text=True, timeout=900,
                        cwd=str(project_dir),
                    )
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    _emit("Node.js dependency install failed or timed out")
        else:
            pm = _detect_node_pm(project_dir)
            _emit(f"installing Node.js dependencies ({pm['run']})...")
            try:
                subprocess.run(
                    pm["install"].split(),
                    capture_output=True, text=True, timeout=900,
                    cwd=str(project_dir),
                )
                _emit("Node.js dependencies installed")
            except (subprocess.TimeoutExpired, FileNotFoundError):
                _emit("Node.js dependency install failed or timed out")

    # ── Go ──
    if (project_dir / "go.mod").exists():
        _emit("downloading Go modules...")
        try:
            env = {**os.environ, "GOPROXY": "https://proxy.golang.org,direct"}
            subprocess.run(
                ["go", "mod", "download"],
                capture_output=True, text=True, timeout=600,
                cwd=str(project_dir), env=env,
            )
            _emit("Go modules downloaded")
        except (subprocess.TimeoutExpired, FileNotFoundError):
            _emit("Go module download failed or timed out")


def _detect_entry_point(project_dir: Path) -> str | None:
    """Detect the main entry point for a project."""
    # Check for common Python entry points
    for entry in ("app.py", "main.py", "server.py", "run.py", "manage.py"):
        if (project_dir / entry).exists():
            return f"python3 {entry}"

    # Check for SvelteKit / Bun / Node.js projects
    if (project_dir / "package.json").exists():
        try:
            pkg = json.loads((project_dir / "package.json").read_text())
            scripts = pkg.get("scripts", {})
            deps = pkg.get("dependencies", {})
            dev_deps = pkg.get("devDependencies", {})

            # SvelteKit: has @sveltejs/kit
            is_sveltekit = "@sveltejs/kit" in deps or "@sveltejs/kit" in dev_deps

            # Check for bun.lock
            has_bun = (project_dir / "bun.lock").exists() or (project_dir / "bun.lockb").exists()

            if is_sveltekit:
                if has_bun:
                    return "__sveltekit_bun__"
                return "__sveltekit_npm__"

            # Regular Node.js
            if "start" in scripts:
                return "npm start"
            if "dev" in scripts:
                return "npm run dev"
        except (json.JSONDecodeError, OSError):
            pass

    # Check for go.mod
    if (project_dir / "go.mod").exists():
        if (project_dir / "main.go").exists():
            return "go run ."

    # Check for Cargo.toml
    if (project_dir / "Cargo.toml").exists():
        return "cargo run"

    return None


def verify_url(url: str, timeout: int = 15) -> tuple[bool, str]:
    """Check if a URL responds with HTTP 200. Returns (ok, detail)."""
    try:
        r = _http_get_with_curl_fallback(url, timeout=timeout, headers={"User-Agent": "ghostprovider/1.0"})
        if r is not None and r.status_code == 200:
            return True, "HTTP 200 OK"
        return False, f"HTTP {r.status_code}" if r else "Connection refused"
    except requests.ConnectionError:
        return False, "Connection refused"
    except requests.Timeout:
        return False, "Timed out"
    except Exception as e:
        return False, str(e)


def _cleanup_strategy(result: HostResult) -> None:
    """Remove services started by a failed strategy attempt."""
    if result.service_names:
        for service_name in result.service_names:
            try:
                subprocess.run(
                    ["systemctl", "--user", "stop", service_name],
                    capture_output=True, text=True, timeout=10,
                )
                subprocess.run(
                    ["systemctl", "--user", "disable", service_name],
                    capture_output=True, text=True, timeout=10,
                )
                # Remove unit file
                unit_file = os.path.expanduser(f"~/.config/systemd/user/{service_name}.service")
                if os.path.isfile(unit_file):
                    os.remove(unit_file)
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                pass

        # Reload systemd daemon
        try:
            subprocess.run(
                ["systemctl", "--user", "daemon-reload"],
                capture_output=True, text=True, timeout=10,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass


def cleanup(analysis: RepoAnalysis, service_names: list[str] | None = None) -> None:
    """Clean up services and clone directory."""
    if service_names:
        for service_name in service_names:
            try:
                subprocess.run(
                    ["systemctl", "--user", "stop", service_name],
                    capture_output=True, text=True, timeout=10,
                )
                subprocess.run(
                    ["systemctl", "--user", "disable", service_name],
                    capture_output=True, text=True, timeout=10,
                )
                # Remove unit file
                unit_file = os.path.expanduser(f"~/.config/systemd/user/{service_name}.service")
                if os.path.isfile(unit_file):
                    os.remove(unit_file)
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                pass

        # Reload systemd daemon
        try:
            subprocess.run(
                ["systemctl", "--user", "daemon-reload"],
                capture_output=True, text=True, timeout=10,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    if analysis.clone_path and os.path.isdir(analysis.clone_path):
        shutil.rmtree(analysis.clone_path, ignore_errors=True)
