.PHONY: test check format lint typecheck install dev clean build publish

test:
	uv run pytest

check: lint typecheck
	@echo "All checks passed!"

lint:
	uv run ruff check

format:
	uv run ruff format

format-check:
	uv run ruff format --check

typecheck:
	uv run ty check

fix:
	uv run ruff check --fix
	uv run ruff format

install:
	uv sync

dev:
	uv sync --group dev

clean:
	rm -rf dist/ build/ *.egg-info .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

build: clean
	uv build

publish: build
	uv publish
