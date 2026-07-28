"""GitHub repository analysis & hosting logic."""

from demo_ghostprovider.hoster.analysis import (
    HostResult,
    RepoAnalysis,
    CATEGORY_KEYWORDS,
    NOT_WEB_TOPICS,
    parse_github_url,
    detect_app_category,
    _deep_analyze_project,
    _compute_host_score,
    _can_host_verdict,
    _detect_language,
    _parse_requirements_txt,
    _parse_pyproject_toml_deps,
    _collect_node_deps,
    _collect_go_deps,
    _collect_rust_deps,
    _scan_python_source,
    _scan_node_source,
    _scan_go_source,
    _scan_rust_source,
    _is_library_project,
    _check_root_files_via_api,
    fetch_repo_metadata,
)

from demo_ghostprovider.hoster.deploy import (
    analyze_repo,
    host_project,
    verify_deployment,
    cleanup,
    preflight_check,
    ensure_cloned,
)

from demo_ghostprovider.hoster.verify import verify_url
from demo_ghostprovider.hoster._helpers import find_free_port

__all__ = [
    "HostResult",
    "RepoAnalysis",
    "CATEGORY_KEYWORDS",
    "NOT_WEB_TOPICS",
    "parse_github_url",
    "detect_app_category",
    "analyze_repo",
    "host_project",
    "verify_deployment",
    "verify_url",
    "cleanup",
    "preflight_check",
    "ensure_cloned",
    "find_free_port",
]
