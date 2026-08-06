"""Deployment pipeline: acquire & analyze, preflight, host, finalize."""

from demo_ghostprovider.hoster.pipeline.analyze import analyze_repo, ensure_cloned
from demo_ghostprovider.hoster.pipeline.finalize import cleanup
from demo_ghostprovider.hoster.pipeline.host import host_project
from demo_ghostprovider.hoster.pipeline.preflight import preflight_check

__all__ = [
    "analyze_repo",
    "host_project",
    "cleanup",
    "preflight_check",
    "ensure_cloned",
]
