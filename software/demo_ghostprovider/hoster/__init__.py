"""GitHub repository analysis & hosting logic."""

from demo_ghostprovider.hoster.models import HostResult, RepoAnalysis
from demo_ghostprovider.hoster.categories import (
    CATEGORY_KEYWORDS,
    NOT_WEB_TOPICS,
    detect_app_category,
    _detect_language,
)
from demo_ghostprovider.hoster.github import (
    parse_github_url,
    fetch_repo_metadata,
    _check_root_files_via_api,
)
from demo_ghostprovider.hoster.scanners.project import (
    _deep_analyze_project,
    _is_library_project,
)
from demo_ghostprovider.hoster.scanners.python import (
    _parse_requirements_txt,
    _parse_pyproject_toml_deps,
    _scan_python_source,
)
from demo_ghostprovider.hoster.scanners.node import (
    _collect_node_deps,
    _scan_node_source,
)
from demo_ghostprovider.hoster.scanners.go import (
    _collect_go_deps,
    _scan_go_source,
)
from demo_ghostprovider.hoster.scanners.rust import (
    _collect_rust_deps,
    _scan_rust_source,
)
from demo_ghostprovider.hoster.scoring import (
    _compute_host_score,
    _can_host_verdict,
)

from demo_ghostprovider.hoster.pipeline import (
    analyze_repo,
    cleanup,
    ensure_cloned,
    host_project,
    preflight_check,
)

from demo_ghostprovider.hoster.verify import verify_deployment, verify_url
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
