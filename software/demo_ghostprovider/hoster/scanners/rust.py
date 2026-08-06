"""Rust dependency and source-code analysis."""

import re
from pathlib import Path
from typing import Any

RUST_WEB_DEPS: set[str] = {
    "actix-web", "axum", "rocket", "warp", "tide", "salvo",
    "poem", "trillium", "nickel", "iron", "gotham", "tiny_http",
    "actix-rt",
}

RUST_CLI_DEPS: set[str] = {
    "clap", "structopt", "argh", "gumdrop",
}

RUST_GUI_DEPS: set[str] = {
    "tauri", "egui", "iced", "druid", "gtk",
}


def _collect_rust_deps(project_dir: Path) -> set[str]:
    cargo = project_dir / "Cargo.toml"
    if not cargo.exists():
        return set()
    try:
        deps: set[str] = set()
        content = cargo.read_text()
        in_deps = False
        for line in content.splitlines():
            if re.match(r"^\[dependencies\]", line):
                in_deps = True
                continue
            if in_deps:
                if re.match(r"^\[", line):
                    break
                m = re.match(r'([a-zA-Z0-9_-]+)\s*=', line.strip())
                if m:
                    deps.add(m.group(1).lower().replace("_", "-"))
        return deps
    except OSError:
        return set()


def _scan_rust_source(project_dir: Path) -> dict[str, Any]:
    info: dict[str, Any] = {
        "has_http_server": False,
        "has_cli": False,
        "is_library": False,
        "has_desktop_gui": False,
    }
    http_patterns = [
        r"actix_web::", r"axum::", r"rocket::", r"warp::filter",
        r"warp::path", r"Tide::new", r"salvo::",
        r"HttpServer::new", r"Server::bind",
    ]
    cli_patterns = [
        r"clap::", r"StructOpt", r"argh::",
    ]
    for rsfile in project_dir.rglob("*.rs"):
        if rsfile.stat().st_size > 100000:
            continue
        try:
            content = rsfile.read_text(errors="replace")
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
