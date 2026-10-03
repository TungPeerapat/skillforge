.PHONY: install run test lint migrate

install:
	uv sync --all-extras

run:
	uv run uvicorn app.main:app --reload

test:
	uv run pytest

lint:
	uv run ruff check .

migrate:
	uv run alembic upgrade head
