"""Repository acquisition & analysis — fetch metadata, clone, detect project files."""

import os
import re
import shutil
import tempfile

from demo_ghostprovider.hoster.categories import _detect_language, detect_app_category
from demo_ghostprovider.hoster.github import (
    _check_root_files_via_api,
    fetch_repo_metadata,
    parse_github_url,
)
from demo_ghostprovider.hoster.git import _git_clone
from demo_ghostprovider.hoster.models import RepoAnalysis
from demo_ghostprovider.hoster.scanners.project import _deep_analyze_project
from demo_ghostprovider.hoster.scoring import _can_host_verdict


def _sanitize_dirname(name: str) -> str:
    """Sanitize a repo name for safe use as a directory name."""
    return re.sub(r'[^a-zA-Z0-9_-]', '_', name)


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


_ALLOWED_REPOS: frozenset[tuple[str, str]] = frozenset({
    ("VERT-sh", "VERT"),
    ("searxng", "searxng"),
    ("usememos", "memos"),
})


def analyze_repo(url: str, work_dir: str | None = None) -> RepoAnalysis:
    """Analyze a GitHub repository for hosting compatibility."""
    result = RepoAnalysis(url=url)

    parsed = parse_github_url(url)
    if parsed and (parsed[0], parsed[1]) not in _ALLOWED_REPOS:
        result.errors.append(
            "This demo version only supports:\n"
            "  • VERT (VERT-sh/VERT)\n"
            "  • SearXNG (searxng/searxng)\n"
            "  • Memos (usememos/memos)"
        )
        result.reason = "Repository not in demo whitelist"
        return result
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
