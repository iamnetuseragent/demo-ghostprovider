.PHONY: clean run install test dist

VERSION := $(shell python3 -c "import tomllib;print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])" 2>/dev/null || echo 0.0.0)

clean:
	find . -type d -name __pycache__ -not -path './.venv/*' -not -path './.git/*' -exec rm -rf {} +
	find . -type d -name '*.egg-info' -not -path './.venv/*' -not -path './.git/*' -exec rm -rf {} +
	rm -rf build dist .pytest_cache .ruff_cache

run:
	PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m demo_ghostprovider

install:
	.venv/bin/pip install -e .

test:
	PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/
	$(MAKE) clean

# Release tarball from tracked files only — never includes .venv, caches,
# egg-info, or build artifacts (git archive packs exactly what git tracks).
dist:
	rm -rf dist
	mkdir -p dist
	git archive --format=tar.gz --prefix=demo-ghostprovider-$(VERSION)/ -o dist/demo-ghostprovider-$(VERSION).tar.gz HEAD
	@echo "Built dist/demo-ghostprovider-$(VERSION).tar.gz"
