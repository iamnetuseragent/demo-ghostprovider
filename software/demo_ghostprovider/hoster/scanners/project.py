"""Project-level deep analysis: library detection and full scan."""

import json
from pathlib import Path
from typing import Any

from demo_ghostprovider.hoster.models import RepoAnalysis
from demo_ghostprovider.hoster.scanners.go import (
    GO_CLI_DEPS, GO_WEB_DEPS, _collect_go_deps, _scan_go_source,
)
from demo_ghostprovider.hoster.scanners.node import (
    NODE_CLI_DEPS, NODE_GUI_DEPS, NODE_WEB_DEPS, _collect_node_deps, _scan_node_source,
)
from demo_ghostprovider.hoster.scanners.python import (
    PYTHON_CLI_DEPS, PYTHON_GUI_DEPS, PYTHON_WEB_DEPS,
    _collect_python_deps, _scan_python_source,
)
from demo_ghostprovider.hoster.scanners.rust import (
    RUST_CLI_DEPS, RUST_GUI_DEPS, RUST_WEB_DEPS, _collect_rust_deps, _scan_rust_source,
)


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

    # ── 4. Store in analysis ──
    analysis.deep_analysis = da
    analysis.web_framework = da.get("web_framework", "")
    analysis.has_http_server = da.get("has_http_server", False)
    analysis.has_cli = da.get("has_cli", False)
    analysis.is_library = da.get("is_library", False)
    analysis.has_desktop_gui = da.get("has_desktop_gui", False)

    return analysis
