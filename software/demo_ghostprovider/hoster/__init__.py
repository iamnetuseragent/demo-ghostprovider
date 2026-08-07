"""Curated demo hosting — supports exactly three services (VERT, SearXNG, Memos)."""

from demo_ghostprovider.hoster._helpers import find_free_port
from demo_ghostprovider.hoster.deploy import cleanup, deploy_service
from demo_ghostprovider.hoster.github import parse_github_url
from demo_ghostprovider.hoster.models import HostResult, RepoAnalysis
from demo_ghostprovider.hoster.preflight import preflight_check
from demo_ghostprovider.hoster.recipes import (
    DEMO_SERVICES,
    DemoRecipe,
    find_recipe,
    resolve_service,
)
from demo_ghostprovider.hoster.verify import verify_deployment, verify_url

__all__ = [
    "DEMO_SERVICES",
    "DemoRecipe",
    "HostResult",
    "RepoAnalysis",
    "cleanup",
    "deploy_service",
    "find_free_port",
    "find_recipe",
    "parse_github_url",
    "preflight_check",
    "resolve_service",
    "verify_deployment",
    "verify_url",
]
