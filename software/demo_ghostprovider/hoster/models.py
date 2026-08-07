"""Data models for demo service resolution and deployment results."""

from dataclasses import dataclass, field


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
    language: str = ""
    can_host: bool = False
    reason: str = ""
    clone_path: str | None = None
    errors: list[str] = field(default_factory=list)
    _temp_base: str | None = None
