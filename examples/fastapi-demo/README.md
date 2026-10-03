# FastAPI Demo (SkillForge example)

A small but realistic FastAPI service used to demonstrate SkillForge end to end.

## Stack

- FastAPI + Uvicorn (Python 3.12)
- SQLAlchemy + Alembic + PostgreSQL
- Redis cache
- Docker Compose for local dependencies
- GitHub Actions CI

## Setup

```bash
uv sync --all-extras
cp .env.example .env
docker compose up -d db cache
uv run alembic upgrade head
```

## Development

```bash
uv run uvicorn app.main:app --reload
```

## Tests

```bash
uv run pytest
uv run ruff check .
uv run mypy app
```
