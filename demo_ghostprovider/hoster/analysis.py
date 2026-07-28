"""GitHub repository analysis & scoring."""

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import requests

from demo_ghostprovider.hoster._helpers import _read_package_json


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
    has_pyproject: bool = False
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
    _temp_base: str | None = None  # temp dir created when work_dir is None


def parse_github_url(url: str) -> tuple[str, str] | None:
    m = GITHUB_URL_RE.match(url.strip())
    if m:
        return m.group(1), m.group(2).rstrip("/")
    return None


def _is_allowed_url(url: str) -> bool:
    """Check if URL is allowed (prevents SSRF)."""
    allowed_hosts = {"api.github.com", "github.com", "raw.githubusercontent.com",
                     "127.0.0.1", "localhost"}
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        return parsed.hostname in allowed_hosts
    except Exception:
        return False


def _http_get_with_curl_fallback(url: str, timeout: int = 10,
                                  headers: dict[str, str] | None = None,
                                  retries: int = 2) -> requests.Response | None:
    """GET a URL with retry, falling back to curl when Python SSL is broken.

    Automatically injects a ``GITHUB_TOKEN`` (or ``GH_TOKEN``) for GitHub API
    calls so the 60-req/h unauthenticated rate limit is avoided.
    Only allows requests to whitelisted hosts (SSRF protection).
    """
    if not _is_allowed_url(url):
        return None

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
    if analysis.has_requirements or analysis.has_pyproject:
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

    # ── 4. Open WebUI detection (self-hosted AI interface) ──
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


def _detect_language(analysis: RepoAnalysis) -> str:
    # Python first — many projects have package.json for frontend tooling
    if analysis.has_requirements or analysis.has_pyproject:
        return "Python"
    if analysis.has_package_json:
        return "Node.js"
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
    if analysis.has_requirements or analysis.has_pyproject or (analysis.clone_path and (Path(analysis.clone_path) / "requirements.txt").exists()):
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

    # ── Negative signals ──
    has_strong_web = bool(wf) or da.get("has_http_server")

    # Libraries are always penalized (even if they have HTTP server code internally)
    if da.get("is_library"):
        score -= 50
        reasons.append("project structure is a library (-50)")

    if not has_strong_web:
        if da.get("has_desktop_gui") or (da.get("gui_dep")):
            score -= 50
            reasons.append("desktop/GUI detected (-50)")
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
    # Libraries with low scores cannot be hosted
    if analysis.deep_analysis.get("is_library") and score < 50:
        return False, rec
    if score >= 50:
        return True, rec
    if score >= 20:
        return True, f"LOW CONFIDENCE — {rec}"
    return False, rec
