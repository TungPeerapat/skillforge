# FastAPI Demo App

A small item API used to demonstrate SkillForge analysis.

## Requirements

- Python 3.12+
- Docker (for PostgreSQL and Redis)

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

The API is served at http://localhost:8000 and the interactive docs at `/docs`.

## Tests

```bash
uv run pytest
uv run ruff check .
```

## Known gaps

- The production deployment pipeline is not documented here.
