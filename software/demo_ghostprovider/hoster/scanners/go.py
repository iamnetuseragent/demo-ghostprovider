"""Go dependency and source-code analysis."""

import re
from pathlib import Path
from typing import Any

GO_WEB_DEPS: set[str] = {
    "gin", "fiber", "echo", "chi", "gorilla/mux", "beego",
    "revel", "buffalo", "iris", "httprouter", "negroni",
    "gin-gonic/gin", "gofiber/fiber", "labstack/echo", "go-chi/chi",
    "gorilla/mux",
}

GO_CLI_DEPS: set[str] = {
    "cobra", "urfave/cli", "pflag",
}


def _collect_go_deps(project_dir: Path) -> set[str]:
    gomod = project_dir / "go.mod"
    if not gomod.exists():
        return set()
    try:
        deps: set[str] = set()
        for line in gomod.read_text().splitlines():
            # Formats:
            #   "    github.com/gin-gonic/gin v1.9.0"
            #   "require github.com/gin-gonic/gin v1.9.0"
            #   '	github.com/gin-gonic/gin v1.9.0'
            m = re.search(r'\s*([a-zA-Z0-9_.-]+(?:\/[a-zA-Z0-9_.-]+)*)\s+v', line)
            if m:
                full = m.group(1)
                deps.add(full)
                # Also add short form (strip known hosting prefixes)
                short = re.sub(r'^(github\.com|gopkg\.in|gitlab\.com|bitbucket\.org)/', '', full)
                if short != full:
                    deps.add(short)
        return deps
    except OSError:
        return set()


def _scan_go_source(project_dir: Path) -> dict[str, Any]:
    info: dict[str, Any] = {
        "has_http_server": False,
        "has_cli": False,
        "is_library": False,
        "has_desktop_gui": False,
    }
    http_patterns = [
        r"http\.ListenAndServe", r"http\.ListenAndServeTLS",
        r"gin\.Default\s*\(", r"gin\.New\s*\(", r"fiber\.New\s*\(",
        r"echo\.New\s*\(", r"chi\.NewRouter", r"mux\.NewRouter",
        r"beego\.Run", r"iris\.New", r"buffalo",
    ]
    cli_patterns = [
        r"cobra\.Command", r"cobra\.Execute", r"flag\.",
        r"pflag\.", r"cli\.App",
    ]
    for gofile in project_dir.rglob("*.go"):
        if gofile.stat().st_size > 100000:
            continue
        try:
            content = gofile.read_text(errors="replace")
            for pat in http_patterns:
                if re.search(pat, content):
                    info["has_http_server"] = True
                    break
            for pat in cli_patterns:
                if re.search(pat, content):
                    info["has_cli"] = True
                    break
        except (OSError, UnicodeDecodeError):
            continue
    return info
