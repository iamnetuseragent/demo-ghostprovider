"""Hosting strategies: Hoster classes, registry, and priority selection."""

from demo_ghostprovider.hoster.models import RepoAnalysis

from demo_ghostprovider.hoster.strategies.base import Hoster
from demo_ghostprovider.hoster.strategies.registry import (
    STRATEGY_REGISTRY,
    available_strategies,
    canonical_strategy,
    get_strategy,
    register,
)

from demo_ghostprovider.hoster.strategies.go import GoHoster
from demo_ghostprovider.hoster.strategies.node import NodeHoster
from demo_ghostprovider.hoster.strategies.python import PythonHoster
from demo_ghostprovider.hoster.strategies.rust import RustHoster
from demo_ghostprovider.hoster.strategies.static import StaticHoster


def _strategy_priority(analysis: RepoAnalysis) -> list[str]:
    """Return strategy names ordered by priority for the given analysis."""
    da = analysis.deep_analysis or {}
    wf = da.get("web_framework", "")

    has_python = analysis.has_requirements or analysis.has_pyproject

    if has_python and wf:
        strategies = ["Python"]
        if analysis.has_go_mod:
            strategies.append("Go")
        if analysis.has_package_json:
            strategies.append("Node.js")
        if analysis.has_cargo:
            strategies.append("Rust")
        if analysis.has_index:
            strategies.append("Static")
        return strategies

    if analysis.has_go_mod:
        return ["Go"]
    if analysis.has_package_json:
        if analysis.has_cargo:
            return ["Node.js", "Rust"]
        return ["Node.js"]
    if analysis.has_cargo:
        return ["Rust"]
    if has_python:
        return ["Python"]
    if analysis.has_index:
        return ["Static"]
    return []


__all__ = [
    "Hoster",
    "STRATEGY_REGISTRY",
    "GoHoster",
    "NodeHoster",
    "PythonHoster",
    "RustHoster",
    "StaticHoster",
    "available_strategies",
    "canonical_strategy",
    "get_strategy",
    "register",
    "_strategy_priority",
]
