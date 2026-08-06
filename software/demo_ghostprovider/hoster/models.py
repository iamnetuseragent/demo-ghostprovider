"""Data models for repository analysis and hosting results."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class HostResult:
    service_names: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    healthy: bool = False
    errors: list[str] = field(default_factory=list)


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
