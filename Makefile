.PHONY: clean run install

clean:
	find . -type d -name __pycache__ -not -path './.venv/*' -not -path './.git/*' -exec rm -rf {} +
	find . -type d -name '*.egg-info' -not -path './.venv/*' -not -path './.git/*' -exec rm -rf {} +
	rm -rf build .pytest_cache .ruff_cache

run:
	PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m demo_ghostprovider

install:
	.venv/bin/pip install -e .
