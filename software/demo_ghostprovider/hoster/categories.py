"""Application category detection."""

from typing import Any

from demo_ghostprovider.hoster.models import RepoAnalysis

CATEGORY_KEYWORDS: dict[str, set[str]] = {
    "media_server": {
        "media", "music", "video", "stream", "streaming",
        "podcast", "audio", "photo", "gallery", "player",
        "jellyfin", "plex", "emby", "blackcandy", "navidrome",
        "airsonic", "funkwhale", "koel",
    },
    "web_app": {
        "web", "website", "frontend", "dashboard", "ui", "app",
        "server", "admin", "panel", "cms", "blog", "forum",
        "wiki", "board",
    },
    "api_server": {
        "api", "backend", "graphql", "rest", "grpc",
    },
    "search_engine": {
        "search", "searx", "searxng", "whoogle", "yacy",
        "librey", "shiori", "gigablast", "manticore",
    },
    "desktop_app": {
        "desktop", "electron", "gtk", "qt", "tui", "tauri",
        "nw.js", "react-native",
    },
    "cli": {
        "cli", "command-line", "console", "terminal",
    },
    "library": {
        "library", "sdk", "framework", "client", "sdk-",
        "plugin", "extension", "middleware",
    },
}

NOT_WEB_TOPICS: set[str] = {
    "desktop-app", "library", "cli", "command-line", "sdk",
    "react-native", "electron-app",
}


def detect_app_category(
    analysis: RepoAnalysis,
    metadata: dict[str, Any] | None,
) -> tuple[str, str, bool]:
    description = (metadata or {}).get("description", "") or ""
    topics = set((metadata or {}).get("topics", []) or [])
    name_lower = analysis.name.lower()
    desc_lower = description.lower()
    combined = f"{name_lower} {desc_lower}"

    desc_kws = {"desktop", "electron", "cli", "command line", "terminal",
                "library", "sdk", "framework"}
    media_kws = {"media", "music", "video", "stream", "streaming",
                 "podcast", "audio", "photo", "gallery", "player",
                 "jellyfin", "plex", "emby", "blackcandy", "navidrome",
                 "airsonic", "funkwhale", "koel"}
    search_kws = {"search", "searx", "searxng", "whoogle", "yacy",
                  "librey", "shiori", "gigablast"}
    web_kws = {"web", "website", "frontend", "dashboard", "ui", "app",
               "server", "admin", "panel", "cms", "blog", "forum",
               "wiki", "board", "api", "backend", "graphql", "rest"}

    # ── Phase 1: Deep analysis signals (most reliable) ──
    if analysis.deep_analysis:
        da = analysis.deep_analysis
        # Web signals first (strongest indicators)
        if da.get("web_framework"):
            return "web_app", f"Deep analysis: {da['web_framework']}", True
        if da.get("has_http_server"):
            return "web_app", "Deep analysis: HTTP server code found", True
        # Non-web signals (only if no web signal present)
        if da.get("has_desktop_gui"):
            return "desktop_app", "Deep analysis: GUI framework detected", False
        if da.get("has_cli") and not da.get("has_http_server"):
            return "cli", "Deep analysis: CLI interface detected", False
        if da.get("is_library"):
            return "library", "Deep analysis: project identified as library", False

    # ── Phase 2: GitHub metadata (fast, no clone needed) ──
    for kw in desc_kws:
        if kw in desc_lower or kw in name_lower:
            if kw in ("library", "sdk", "framework"):
                return "library", f"GitHub description/library keyword: {kw}", False
            if kw in ("desktop", "electron"):
                return "desktop_app", f"GitHub description: {kw}", False
            if kw in ("cli", "command line", "terminal"):
                return "cli", f"GitHub description: {kw}", False
    if topics & NOT_WEB_TOPICS:
        topic_str = ", ".join(sorted(topics & NOT_WEB_TOPICS))
        if topics & {"desktop-app", "electron-app"}:
            return "desktop_app", f"GitHub topics: {topic_str}", False
        return "library", f"GitHub topics: {topic_str}", False

    search_topics = {"search-engine", "search", "searx", "searxng", "whoogle", "yacy"}
    if topics & search_topics:
        topic_str = ", ".join(sorted(topics & search_topics))
        return "search_engine", f"GitHub topics: {topic_str}", True

    for kw in search_kws:
        if kw in combined:
            return "search_engine", f"Keyword: {kw}", True
    for kw in media_kws:
        if kw in combined:
            return "media_server", f"Keyword: {kw}", True
    for kw in web_kws:
        if kw in combined:
            return "web_app", f"Keyword: {kw}", True

    # ── Phase 3: File-level fallback ──
    if analysis.has_index:
        return "web_app", "Static site (index.html)", True

    return "unknown", "Could not determine application type from available signals", True


def _detect_language(analysis: RepoAnalysis) -> str:
    # Python first — many projects have package.json for frontend tooling
    if analysis.has_requirements or analysis.has_pyproject:
        return "Python"
    if analysis.has_package_json:
        return "Node.js"
    if analysis.has_go_mod:
        return "Go"
    if analysis.has_cargo:
        return "Rust"
    if analysis.has_index:
        return "Static HTML"
    return "Unknown"
