"""Smoke tests for demo_ghostprovider — imports and curated catalog."""

import sys
from pathlib import Path

import demo_ghostprovider
from demo_ghostprovider.hoster.deploy import _resolve_start
from demo_ghostprovider.hoster.recipes import (
    DEMO_SERVICES,
    find_recipe,
    resolve_service,
)
from demo_ghostprovider.services.scan import _friendly_name


def _recipe(service_name: str):
    for r in DEMO_SERVICES:
        if r.service_name == service_name:
            return r
    raise AssertionError(f"recipe {service_name} not found")


def test_import():
    assert demo_ghostprovider.__name__ == "demo_ghostprovider"


def test_catalog_has_exactly_three_services():
    assert len(DEMO_SERVICES) == 3
    assert {s.service_name for s in DEMO_SERVICES} == {
        "demo-vert",
        "demo-searxng",
        "demo-memos",
    }


def test_find_recipe_case_insensitive():
    assert find_recipe("VERT-sh", "VERT") is not None
    assert find_recipe("searxng", "searxng") is not None
    assert find_recipe("usememos", "memos") is not None
    assert find_recipe("foo", "bar") is None


def test_resolve_service_rejects_unknown():
    analysis, recipe, error = resolve_service("https://github.com/foo/bar")
    assert recipe is None
    assert analysis.errors
    assert error is not None


def test_resolve_service_accepts_known():
    analysis, recipe, _ = resolve_service("https://github.com/usememos/memos")
    assert recipe is not None
    assert analysis.owner == "usememos"
    assert analysis.name == "memos"


def test_vert_recipe_matches_real_repo():
    """VERT-sh/VERT is a Svelte/bun static app, not a Rust project."""
    recipe = _recipe("demo-vert")
    assert recipe.language == "JavaScript"
    assert recipe.build_steps == ("bun install", "bun run build")
    assert "cargo" not in recipe.build_steps


def test_vert_recipe_pre_builds_env():
    """VERT needs .env copied from .env.example before `bun run build`."""
    recipe = _recipe("demo-vert")
    assert recipe.pre_build == (
        "if [ -f .env.example ] && [ ! -f .env ]; then cp .env.example .env; fi",
    )


def test_searxng_recipe_builds_from_requirements():
    """SearXNG editable install fails (build env misses msgspec); install
    runtime deps from requirements.txt instead and run via PYTHONPATH."""
    recipe = _recipe("demo-searxng")
    assert recipe.searxng is True
    assert ".venv/bin/pip install --no-cache-dir -r requirements.txt" in recipe.build_steps
    assert "-e ." not in " ".join(recipe.build_steps)
    assert recipe.start_cmd == "{venv} -m searx.webapp"
    assert recipe.port == 8888


def test_searxng_resolve_start_keeps_venv():
    recipe = _recipe("demo-searxng")
    cmd = _resolve_start(recipe, Path("/srv/searxng"), 8888)
    assert cmd == "/srv/searxng/.venv/bin/python -m searx.webapp"


def test_memos_recipe_go_build_target():
    """go build must target ./cmd/memos (no Go files at repo root)."""
    recipe = _recipe("demo-memos")
    assert "go build -o ghost-server ./cmd/memos" in recipe.build_steps
    assert "go build -o ghost-server" not in recipe.build_steps


def test_memos_recipe_start_cmd():
    """memos has no --mode flag; just --port."""
    recipe = _recipe("demo-memos")
    assert "--mode" not in recipe.start_cmd
    assert recipe.start_cmd == "{bin} --port {port}"


def test_resolve_start_memos():
    recipe = _recipe("demo-memos")
    cmd = _resolve_start(recipe, Path("/srv/memos"), 5230)
    assert cmd == "/srv/memos/ghost-server --port 5230"


def test_resolve_start_vert():
    recipe = _recipe("demo-vert")
    cmd = _resolve_start(recipe, Path("/srv/vert"), 8088)
    assert cmd == (
        f"{sys.executable} -m http.server 8088 "
        f"--bind 127.0.0.1 --directory /srv/vert/build"
    )


def test_friendly_name_matches_service_name():
    assert _friendly_name("demo-vert", "") == "VERT"
    assert _friendly_name("demo-searxng", "") == "SearXNG"
    assert _friendly_name("demo-memos", "") == "Memos"


def test_friendly_name_matches_repo_url():
    assert _friendly_name("ghost-py-17ae7056", "https://github.com/searxng/searxng") == "SearXNG"
    assert _friendly_name("ghost-js-6a53ddda", "https://github.com/VERT-sh/VERT") == "VERT"
    assert _friendly_name("ghost-go-aa69fdc5", "https://github.com/usememos/memos") == "Memos"


def test_friendly_name_fallback_to_unit_name():
    assert _friendly_name("ghost-py-unknown", "") == "ghost-py-unknown"
