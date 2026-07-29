"""Deployment orchestrator — ties together analysis, strategies, and verification."""

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from demo_ghostprovider.hoster.analysis import (
    RepoAnalysis, HostResult, _deep_analyze_project, _can_host_verdict,
    _detect_language,
)
from demo_ghostprovider.hoster.git import _git_clone


def _sanitize_dirname(name: str) -> str:
    """Sanitize a repo name for safe use as a directory name."""
    return re.sub(r'[^a-zA-Z0-9_-]', '_', name)
from demo_ghostprovider.hoster.systemd import _cleanup_strategy
from demo_ghostprovider.hoster.verify import verify_deployment
from demo_ghostprovider.hoster.strategies import _strategy_priority
from demo_ghostprovider.hoster.strategies.openwebui import _host_openwebui_systemd
from demo_ghostprovider.hoster.strategies.python import _host_python_systemd
from demo_ghostprovider.hoster.strategies.node import _host_node_systemd
from demo_ghostprovider.hoster.strategies.go import _host_go_systemd
from demo_ghostprovider.hoster.strategies.rust import _host_rust_systemd
from demo_ghostprovider.hoster.strategies.static import _host_static_systemd
from demo_ghostprovider.hoster._helpers import find_free_port
from demo_ghostprovider.service_defs import get_service_def, verify_repo_integrity
from demo_ghostprovider.state import register as _register_state


def _detect_project_files(analysis: "RepoAnalysis", items: list[str]) -> None:
    """Set has_* fields based on files in the project directory."""
    analysis.has_package_json = "package.json" in items
    analysis.has_requirements = "requirements.txt" in items
    analysis.has_pyproject = "pyproject.toml" in items
    analysis.has_go_mod = "go.mod" in items
    analysis.has_cargo = "Cargo.toml" in items
    analysis.has_index = any(f in items for f in ("index.html", "index.htm", "index.php"))


def _build_git_url(owner: str, name: str) -> str:
    """Build a git clone URL without embedding credentials.

    Credentials are passed via GIT_ASKPASS in ``_git_clone()`` to avoid
    leaking the token through ``/proc/PID/cmdline``.
    """
    return f"https://github.com/{owner}/{name}.git"


_ALLOWED_REPOS: frozenset[str] = frozenset({
    "https://github.com/VERT-sh/VERT",
    "https://github.com/searxng/searxng",
    "https://github.com/usememos/memos",
    "https://github.com/open-webui/open-webui",
})


def analyze_repo(url: str, work_dir: str | None = None) -> RepoAnalysis:
    """Analyze a GitHub repository for hosting compatibility."""
    from demo_ghostprovider.hoster.analysis import (
        parse_github_url, fetch_repo_metadata, _check_root_files_via_api,
        detect_app_category,
    )

    result = RepoAnalysis(url=url)

    # Whitelist enforcement — only pre-approved repos can be deployed
    norm_url = url.rstrip("/")
    if norm_url.endswith(".git"):
        norm_url = norm_url[:-4]
    if norm_url not in _ALLOWED_REPOS:
        result.errors.append(
            "This demo version only supports the following repositories:\n"
            + "\n".join(f"  • {r}" for r in sorted(_ALLOWED_REPOS))
        )
        result.reason = "Repository not in demo whitelist"
        return result

    parsed = parse_github_url(url)
    if not parsed:
        result.errors.append("Invalid GitHub URL format")
        result.reason = "Invalid GitHub URL"
        return result

    result.owner, result.name = parsed

    metadata, meta_error = fetch_repo_metadata(result.owner, result.name)
    if metadata is None:
        metadata = {}
        result.errors.append(meta_error or "Repository not found")
    result.exists = True

    root_files = _check_root_files_via_api(result.owner, result.name)
    if root_files is not None:
        result.has_package_json = "package.json" in root_files
        result.has_requirements = "requirements.txt" in root_files
        result.has_pyproject = "pyproject.toml" in root_files
        result.has_go_mod = "go.mod" in root_files
        result.has_cargo = "Cargo.toml" in root_files
        result.has_index = any(f in root_files for f in ("index.html", "index.htm", "index.php"))

    already_cloned = False
    if work_dir:
        base = os.path.abspath(os.path.expanduser(work_dir))
    else:
        # Use real filesystem, not /tmp (which may be tmpfs with quota)
        tmp_base = os.path.expanduser("~/localhosts/.tmp")
        os.makedirs(tmp_base, exist_ok=True)
        base = tempfile.mkdtemp(prefix="demo_ghostprovider-", dir=tmp_base)
        result._temp_base = base
    os.makedirs(base, exist_ok=True)
    # Sanitize repo name to prevent path traversal
    safe_name = _sanitize_dirname(result.name)
    clone_dir = os.path.join(base, safe_name)
    if os.path.isdir(os.path.join(clone_dir, ".git")):
        already_cloned = True
    elif os.path.isdir(clone_dir):
        shutil.rmtree(clone_dir, ignore_errors=True)

    if not already_cloned:
        git_url = _build_git_url(result.owner, result.name)
        if not _git_clone(git_url, clone_dir):
            result.errors.append("git clone failed after retries (check network connection)")
            result.reason = "Cannot clone repository"
            return result

    result.clone_path = clone_dir
    items = os.listdir(clone_dir)

    _detect_project_files(result, items)

    result.language = _detect_language(result)

    result = _deep_analyze_project(result)

    result.can_host, result.reason = _can_host_verdict(result)

    result.errors = [e for e in result.errors if e != (meta_error or "Repository not found")]

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
    elif analysis._temp_base:
        base = analysis._temp_base
    else:
        base = os.path.expanduser("~/localhosts")
    os.makedirs(base, exist_ok=True)
    # Sanitize repo name to prevent path traversal
    safe_name = _sanitize_dirname(analysis.name)
    clone_dir = os.path.join(base, safe_name)
    if os.path.isdir(os.path.join(clone_dir, ".git")):
        analysis.clone_path = clone_dir
        items = os.listdir(clone_dir)
        _detect_project_files(analysis, items)
        return
    elif os.path.isdir(clone_dir):
        shutil.rmtree(clone_dir, ignore_errors=True)

    git_url = _build_git_url(analysis.owner, analysis.name)

    if not _git_clone(git_url, clone_dir):
        raise RuntimeError("git clone failed after retries (check network connection)")

    analysis.clone_path = clone_dir
    items = os.listdir(clone_dir)
    _detect_project_files(analysis, items)


def preflight_check() -> list[str]:
    """Run pre-flight checks before deployment. Returns list of issues."""
    issues: list[str] = []

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


def host_project(analysis: RepoAnalysis, port: int = 0,
                 verify: bool = True, work_dir: str | None = None,
                 on_status: Callable[[str], None] | None = None,
                 sudo_password: bytearray | None = None) -> HostResult:
    """Run the project and return service names and URLs."""
    def _emit(msg: str) -> None:
        if on_status:
            on_status(msg)

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

    # Verify repo integrity if a service definition with sha256/ref exists
    svc_def = get_service_def(analysis.url)
    if svc_def:
        integrity_errors = verify_repo_integrity(analysis.clone_path, svc_def)
        if integrity_errors:
            result = HostResult()
            result.errors.extend(integrity_errors)
            return result

    if not analysis.deep_analysis:
        _deep_analyze_project(analysis)
        analysis.can_host, analysis.reason = _can_host_verdict(analysis)

    if port == 0:
        port = find_free_port()

    project_dir = Path(analysis.clone_path)
    repo_url = analysis.url

    # Check if service definition exists in ghostproviderfile
    svc_def = get_service_def(repo_url)
    if svc_def:
        _emit(f"using service definition: {svc_def.name}")
        if svc_def.port:
            port = svc_def.port
        strategy = svc_def.strategy
    else:
        strategies = _strategy_priority(analysis)
        strategy = strategies[0] if strategies else None

    # Map ghostproviderfile lowercase strategy names to canonical fn_map keys
    _strategy_alias = {
        "python": "Python",
        "node": "Node.js",
        "go": "Go",
        "rust": "Rust",
        "static": "Static",
        "openwebui": "OpenWebUI",
    }

    # Determine build/start overrides from service definition
    build_cmd = svc_def.build if svc_def else ""
    start_cmd = svc_def.start if svc_def else ""

    fn_map = {
        "OpenWebUI": lambda: _host_openwebui_systemd(project_dir, port, analysis.name, sudo_password=sudo_password),
        "Python": lambda: _host_python_systemd(project_dir, port, analysis.name, build_cmd=build_cmd, start_cmd=start_cmd),
        "Node.js": lambda: _host_node_systemd(project_dir, port, analysis.name, build_cmd=build_cmd, start_cmd=start_cmd),
        "Go": lambda: _host_go_systemd(project_dir, port, analysis.name, build_cmd=build_cmd, start_cmd=start_cmd),
        "Rust": lambda: _host_rust_systemd(project_dir, port, analysis.name),
        "Static": lambda: _host_static_systemd(project_dir, port, analysis.name),
    }

    # Resolve strategy name: map lowercase alias to canonical key
    resolved_strategy = _strategy_alias.get(strategy, strategy) if strategy else None

    # Build strategy list: use service definition or fallback to auto-detection
    if resolved_strategy and resolved_strategy in fn_map:
        strategy_list = [(resolved_strategy, fn_map[resolved_strategy])]
    else:
        strategies = _strategy_priority(analysis)
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
            if verify:
                strategy_result = verify_deployment(strategy_result)
            if strategy_result.healthy or (strategy_result.urls and strategy_result.service_names):
                _register_state(service_name, str(project_dir), repo_url)
                if work_dir:
                    _finalize_temp_dir(analysis, service_name, permanent_base=os.path.abspath(os.path.expanduser(work_dir)), on_status=on_status)
                else:
                    managed_base = os.path.expanduser("~/.local/share/demo-ghostprovider/services")
                    _finalize_temp_dir(analysis, service_name, permanent_base=managed_base, on_status=on_status)
                return strategy_result
            should_cleanup = True
            msg = strategy_result.errors[0] if strategy_result.errors else "service started but health check failed"
            errors.append(f"[{name}] {msg}")
        except RuntimeError as e:
            should_cleanup = True
            errors.append(f"[{name}] {e}")
        except Exception as e:
            should_cleanup = True
            errors.append(f"[{name}] unexpected error: {e}")
        finally:
            if should_cleanup:
                _cleanup_strategy(strategy_result)

    # All strategies failed — clean up temp dir if we created one
    if analysis._temp_base:
        try:
            shutil.rmtree(analysis._temp_base, ignore_errors=True)
        except OSError:
            pass
        analysis._temp_base = None

    raise RuntimeError("All strategies failed:\n" + "\n".join(errors))


def _finalize_temp_dir(analysis: RepoAnalysis, service_name: str,
                       permanent_base: str = "",
                       on_status: Callable[[str], None] | None = None) -> None:
    """Move project from temp dir to a permanent location after successful deploy.

    Updates the systemd unit file to reference the new paths, restarts
    the service, and removes the temp directory.
    """
    if not analysis._temp_base or not analysis.clone_path:
        return

    def _emit(msg: str) -> None:
        if on_status:
            on_status(msg)

    if not permanent_base:
        permanent_base = os.path.expanduser("~/localhosts")
    os.makedirs(permanent_base, exist_ok=True)
    safe_name = _sanitize_dirname(analysis.name)
    final_dir = os.path.join(permanent_base, safe_name)

    # If permanent dir already exists with a different clone, back it up
    if os.path.isdir(final_dir) and os.path.abspath(final_dir) != os.path.abspath(analysis.clone_path):
        backup = final_dir + ".old"
        if os.path.isdir(backup):
            shutil.rmtree(backup, ignore_errors=True)
        os.rename(final_dir, backup)

    _emit(f"moving project to {permanent_base}...")
    try:
        os.rename(analysis.clone_path, final_dir)
    except OSError:
        # Cross-device move fallback
        shutil.copytree(analysis.clone_path, final_dir, dirs_exist_ok=True)
        shutil.rmtree(analysis.clone_path, ignore_errors=True)

    # Update systemd unit file paths
    unit_file = os.path.expanduser(f"~/.config/systemd/user/{service_name}.service")
    if os.path.isfile(unit_file):
        try:
            content = Path(unit_file).read_text()
            old_base = os.path.abspath(analysis.clone_path)
            new_base = os.path.abspath(final_dir)
            content = content.replace(old_base, new_base)
            Path(unit_file).write_text(content)

            subprocess.run(
                ["systemctl", "--user", "daemon-reload"],
                capture_output=True, text=True, timeout=10,
            )
            subprocess.run(
                ["systemctl", "--user", "restart", service_name],
                capture_output=True, text=True, timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired, FileNotFoundError):
            pass

    # Patch any .env files that still reference the old temp dir path
    old_base = os.path.abspath(analysis.clone_path)
    new_base = os.path.abspath(final_dir)
    if old_base != new_base:
        for env_name in (".env", ".env.local", ".env.production"):
            env_path = os.path.join(final_dir, env_name)
            if os.path.isfile(env_path):
                try:
                    env_content = Path(env_path).read_text()
                    if old_base in env_content:
                        env_content = env_content.replace(old_base, new_base)
                        Path(env_path).write_text(env_content)
                except OSError:
                    pass

    # Update analysis
    analysis.clone_path = final_dir

    # Update state registry with new path
    from demo_ghostprovider.state import register as _register_state
    _register_state(service_name, final_dir, analysis.url)

    # Clean up temp dir
    _emit("cleaning up temp directory...")
    try:
        shutil.rmtree(analysis._temp_base, ignore_errors=True)
    except OSError:
        pass
    analysis._temp_base = None


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
                unit_file = os.path.expanduser(f"~/.config/systemd/user/{service_name}.service")
                if os.path.isfile(unit_file):
                    os.remove(unit_file)
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                pass

        try:
            subprocess.run(
                ["systemctl", "--user", "daemon-reload"],
                capture_output=True, text=True, timeout=10,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    if analysis.clone_path and os.path.isdir(analysis.clone_path):
        shutil.rmtree(analysis.clone_path, ignore_errors=True)

    # Also clean up temp dir if it exists
    if analysis._temp_base and os.path.isdir(analysis._temp_base):
        shutil.rmtree(analysis._temp_base, ignore_errors=True)
        analysis._temp_base = None
