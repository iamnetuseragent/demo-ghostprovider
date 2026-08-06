"""Hosting confidence scoring and verdicts."""

from pathlib import Path

from demo_ghostprovider.hoster.models import RepoAnalysis
from demo_ghostprovider.hoster.scanners.node import (
    NODE_CLI_DEPS, NODE_WEB_DEPS, _collect_node_deps,
)
from demo_ghostprovider.hoster.scanners.python import (
    PYTHON_CLI_DEPS, PYTHON_GUI_DEPS, PYTHON_WEB_DEPS, _collect_python_deps,
)


def _compute_host_score(analysis: RepoAnalysis) -> tuple[int, str]:
    """Compute a confidence score (0-100) and recommendation for hosting.

    Positive signals (web app indicators):
      +50  Web framework detected in dependencies
      +40  HTTP server code found in source
      +30  Static site (index.html)
      +20  index.html present
      +15  has_package_json with web deps
      +10  Python with web deps
      +10  GitHub description/web keywords
      +10  media_server keywords
      +10  search_engine keywords

    Negative signals (non-web indicators):
      -40  CLI dependency without web dep
      -50  Library structure (no entry point)
      -50  Desktop/GUI dependency
      -40  CLI source code without HTTP server
      -30  GitHub desktop/CLI keywords
    """
    score = 0
    reasons: list[str] = []
    da = analysis.deep_analysis or {}

    # ── Dependency-level signals ──
    wf = da.get("web_framework", "")
    if wf:
        score += 50
        reasons.append(f"web framework: {wf} (+50)")

    # ── Source code signals ──
    if da.get("has_http_server"):
        score += 40
        reasons.append("HTTP server in source (+40)")

    html_count = 0
    if analysis.clone_path:
        html_count = len(list(Path(analysis.clone_path).rglob("*.html")))

    if analysis.has_index:
        score += 30
        reasons.append("static index.html (+30)")
    elif html_count > 0:
        score += min(15 + html_count, 30)
        reasons.append(f"HTML files ({html_count}) (+{min(15 + html_count, 30)})")

    # ── Language-specific dep signals (use cached data if available) ──
    if analysis.has_package_json:
        nd = da.get("_node_deps") if da.get("_node_deps") is not None else (
            _collect_node_deps(Path(analysis.clone_path)) if analysis.clone_path else None
        )
        if nd:
            web_in_node = set(nd.keys()) & NODE_WEB_DEPS
            if web_in_node:
                score += 15
                reasons.append(f"Node.js web deps: {', '.join(web_in_node)} (+15)")
    if analysis.has_requirements or analysis.has_pyproject or (analysis.clone_path and (Path(analysis.clone_path) / "requirements.txt").exists()):
        pd = da.get("_py_deps") if da.get("_py_deps") is not None else (
            _collect_python_deps(Path(analysis.clone_path)) if analysis.clone_path else set()
        )
        web_in_py = pd & PYTHON_WEB_DEPS
        if web_in_py:
            score += 10
            reasons.append(f"Python web deps: {', '.join(web_in_py)} (+10)")

    # ── GitHub metadata ──
    if da.get("gh_description_web"):
        score += 10
        reasons.append("GitHub description suggests web app (+10)")
    if da.get("gh_topics_media"):
        score += 10
        reasons.append("GitHub topics suggest media server (+10)")
    if da.get("gh_topics_search"):
        score += 10
        reasons.append("GitHub topics suggest search engine (+10)")

    # ── Negative signals ──
    has_strong_web = bool(wf) or da.get("has_http_server")

    # Libraries are always penalized (even if they have HTTP server code internally)
    if da.get("is_library"):
        score -= 50
        reasons.append("project structure is a library (-50)")

    if not has_strong_web:
        if da.get("has_desktop_gui") or (da.get("gui_dep")):
            score -= 50
            reasons.append("desktop/GUI detected (-50)")
        if da.get("has_cli") and not da.get("has_http_server"):
            score -= 40
            reasons.append("CLI interface without HTTP server (-40)")
        if da.get("github_not_web"):
            score -= 30
            reasons.append("GitHub metadata suggests non-web (-30)")

        # ── CLI deps without web deps ──
        if analysis.clone_path:
            if analysis.has_package_json:
                nd = da.get("_node_deps") if da.get("_node_deps") is not None else _collect_node_deps(Path(analysis.clone_path))
                if nd:
                    has_web = bool(set(nd.keys()) & NODE_WEB_DEPS)
                    has_cli = bool(set(nd.keys()) & NODE_CLI_DEPS)
                    if has_cli and not has_web:
                        score -= 40
                        reasons.append("Node CLI deps without web deps (-40)")
            pd = da.get("_py_deps") if da.get("_py_deps") is not None else (
                _collect_python_deps(Path(analysis.clone_path)) if analysis.clone_path else set()
            )
            has_py_cli = bool(pd & PYTHON_CLI_DEPS)
            has_py_web = bool(pd & PYTHON_WEB_DEPS)
            if has_py_cli and not has_py_web:
                score -= 40
                reasons.append("Python CLI deps without web deps (-40)")
            has_py_gui = bool(pd & PYTHON_GUI_DEPS)
            if has_py_gui:
                score -= 50
                reasons.append("Python GUI deps (-50)")

    # ── Score-based verdict ──
    if score >= 50:
        return score, f"high confidence ({score}/100): " + "; ".join(reasons[:3])
    elif score >= 20:
        return score, f"low confidence ({score}/100): " + "; ".join(reasons[:3])
    else:
        return score, f"unsuitable ({score}/100): " + "; ".join(reasons[:3] if reasons else ["no web indicators found"])


def _can_host_verdict(analysis: RepoAnalysis) -> tuple[bool, str]:
    score, rec = _compute_host_score(analysis)
    analysis.host_score = score
    analysis.host_recommendation = rec
    # Libraries with low scores cannot be hosted
    if analysis.deep_analysis.get("is_library") and score < 50:
        return False, rec
    if score >= 50:
        return True, rec
    if score >= 20:
        return True, f"LOW CONFIDENCE — {rec}"
    return False, rec
