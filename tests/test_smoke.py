"""Smoke tests for demo_ghostprovider — imports and curated catalog."""

import demo_ghostprovider
from demo_ghostprovider.hoster.recipes import (
    DEMO_SERVICES,
    find_recipe,
    resolve_service,
)


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
