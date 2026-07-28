"""Hosting strategy priority selection."""

from demo_ghostprovider.hoster.analysis import RepoAnalysis


def _strategy_priority(analysis: RepoAnalysis) -> list[str]:
    """Return strategy names ordered by priority for the given analysis."""
    da = analysis.deep_analysis or {}
    wf = da.get("web_framework", "")

    if analysis.deep_analysis.get("is_openwebui"):
        return ["OpenWebUI"]

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
