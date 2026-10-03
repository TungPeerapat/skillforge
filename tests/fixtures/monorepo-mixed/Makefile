.PHONY: install test lint

install:
	cd services/api && uv sync --all-extras
	cd services/web && pnpm install

test:
	cd services/api && uv run pytest
	cd services/web && pnpm test

lint:
	cd services/api && uv run ruff check .
