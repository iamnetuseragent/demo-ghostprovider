"""Node.js dependency and source-code analysis."""

import json
import re
from pathlib import Path
from typing import Any

NODE_WEB_DEPS: set[str] = {
    "express", "next", "nuxt", "fastify", "koa", "hapi", "sails",
    "meteor", "restify", "feathers", "adonisjs", "loopback",
    "moleculer", "derby", "total.js",
    "@sveltejs/kit", "@angular/core", "@nestjs/core",
    "gatsby", "remix", "astro", "svelte", "vue", "react",
    "angular", "preact", "solid-js",
    "strapi", "keystone", "ghost", "directus", "payload",
    "next-server", "nuxt3", "vue-router",
}

NODE_CLI_DEPS: set[str] = {
    "commander", "yargs", "meow", "oclif", "vorpal", "ink",
}

NODE_GUI_DEPS: set[str] = {
    "electron", "electron-builder", "nw.js", "proton-native",
}


def _collect_node_deps(project_dir: Path) -> dict[str, str] | None:
    pkg = project_dir / "package.json"
    if not pkg.exists():
        return None
    try:
        data = json.loads(pkg.read_text())
        all_deps: dict[str, str] = {}
        for section in ("dependencies", "devDependencies", "peerDependencies"):
            all_deps.update(data.get(section, {}))
        return all_deps
    except (json.JSONDecodeError, OSError):
        return None


def _scan_node_source(project_dir: Path) -> dict[str, Any]:
    info: dict[str, Any] = {
        "has_http_server": False,
        "has_cli": False,
        "is_library": False,
        "has_desktop_gui": False,
    }
    http_patterns = [
        r"app\.listen\s*\(", r"server\.listen\s*\(", r"createServer\s*\(",
        r"http\.createServer", r"express\s*\(", r"Fastify\s*\(",
        r"Koa\s*\(", r"socket\.io",
    ]
    cli_patterns = [
        r"commander", r"yargs", r"argv", r"process\.argv",
        r"meow\s*\(", r"oclif",
    ]
    gui_patterns = [
        r"electron", r"nw\.js", r"BrowserWindow",
    ]
    for jsfile in list(project_dir.rglob("*.js")) + list(project_dir.rglob("*.ts")):
        if jsfile.stat().st_size > 100000:
            continue
        if "node_modules" in str(jsfile):
            continue
        try:
            content = jsfile.read_text(errors="replace")
            for pat in http_patterns:
                if re.search(pat, content):
                    info["has_http_server"] = True
                    break
            for pat in cli_patterns:
                if re.search(pat, content):
                    info["has_cli"] = True
                    break
            for pat in gui_patterns:
                if re.search(pat, content):
                    info["has_desktop_gui"] = True
                    break
        except (OSError, UnicodeDecodeError):
            continue
    return info
