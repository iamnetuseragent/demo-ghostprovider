"""Curated demo catalog — exactly three supported services.

demo_ghostprovider does not host arbitrary repositories. Each entry below
is a hardcoded deploy recipe for one specific public service.
"""

from dataclasses import dataclass, field

from demo_ghostprovider.hoster.github import fetch_repo_metadata, parse_github_url
from demo_ghostprovider.hoster.models import RepoAnalysis


@dataclass(frozen=True)
class DemoRecipe:
    """Hardcoded deploy recipe for a single supported demo service."""

    owner: str
    name: str
    language: str
    service_name: str
    description: str
    display_name: str = ""
    pre_build: tuple[str, ...] = ()
    build_steps: tuple[str, ...] = ()
    start_cmd: str = ""
    port: int = 0
    searxng: bool = False
    env: dict[str, str] = field(default_factory=dict)


DEMO_SERVICES: tuple[DemoRecipe, ...] = (
    DemoRecipe(
        owner="VERT-sh",
        name="VERT",
        language="JavaScript",
        service_name="demo-vert",
        description="VERT — next-generation file converter (Svelte)",
        display_name="VERT",
        pre_build=("if [ -f .env.example ] && [ ! -f .env ]; then cp .env.example .env; fi",),
        build_steps=("bun install", "bun run build"),
        start_cmd="{python} -m http.server {port} --bind 127.0.0.1 --directory {project}/build",
    )
    ,
    DemoRecipe(
        owner="searxng",
        name="searxng",
        language="Python",
        service_name="demo-searxng",
        description="SearXNG — privacy-friendly metasearch engine (Python)",
        display_name="SearXNG",
        build_steps=(
            "python3 -m venv --clear .venv",
            ".venv/bin/pip install --no-cache-dir -r requirements.txt",
        )
        ,
        start_cmd="{venv} -m searx.webapp",
        port=8888,
        searxng=True,
    )
    ,
    DemoRecipe(
        owner="usememos",
        name="memos",
        language="Go",
        service_name="demo-memos",
        description="Memos — self-hosted, open-source knowledge base (Go)",
        display_name="Memos",
        build_steps=(
            "pnpm --dir web install",
            "pnpm --dir web release",
            "go build -o ghost-server ./cmd/memos",
        )
        ,
        start_cmd="{bin} --port {port}",
    )
    ,
)


def find_recipe(owner: str, name: str) -> DemoRecipe | None:
    """Return the recipe for a repository, or None if it is not supported."""
    for recipe in DEMO_SERVICES:
        if recipe.owner.lower() == owner.lower() and recipe.name.lower() == name.lower():
            return recipe
    return None


def resolve_service(url: str) -> tuple[RepoAnalysis, DemoRecipe | None, str | None]:
    """Resolve a GitHub URL against the curated demo catalog.

    Returns ``(analysis, recipe, error)``. ``recipe`` is None when the URL
    does not match one of the three supported demo services.
    """
    analysis = RepoAnalysis(url=url.strip())
    parsed = parse_github_url(url)
    if not parsed:
        error = "Invalid GitHub URL format"
        analysis.errors.append(error)
        return analysis, None, error

    analysis.owner, analysis.name = parsed
    recipe = find_recipe(analysis.owner, analysis.name)
    if recipe is None:
        error = (
            "This demo only supports three services:\n"
            "  • VERT — github.com/VERT-sh/VERT\n"
            "  • SearXNG — github.com/searxng/searxng\n"
            "  • Memos — github.com/usememos/memos"
        )
        analysis.errors.append(error)
        return analysis, None, error

    analysis.language = recipe.language
    metadata, meta_error = fetch_repo_metadata(analysis.owner, analysis.name)
    if metadata is None:
        analysis.errors.append(meta_error or "Repository not found")
        return analysis, recipe, None
    analysis.exists = True
    return analysis, recipe, None
