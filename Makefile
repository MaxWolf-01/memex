.PHONY: test check format lint typecheck install dev clean build publish release-patch release-minor release-major

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

define bump_version
	@uv run python -c 'import tomllib; s=open("pyproject.toml","r",encoding="utf-8").read(); v=tomllib.loads(s)["project"]["version"]; a,mi,pa=map(int,v.split(".")); nv=$(1); open("pyproject.toml","w",encoding="utf-8").write(s.replace(f"version = \"{v}\"", f"version = \"{nv}\"", 1)); print(f"Bumped {v} -> {nv}")'
	@VERSION=$$(grep 'version = ' pyproject.toml | head -1 | cut -d'"' -f2) && \
		git add pyproject.toml && \
		git commit -m "Release v$$VERSION" && \
		git tag "v$$VERSION" && \
		git push && git push --tags
endef

release-patch:
	$(call bump_version,f"{a}.{mi}.{pa+1}")

release-minor:
	$(call bump_version,f"{a}.{mi+1}.0")

release-major:
	$(call bump_version,f"{a+1}.0.0")
