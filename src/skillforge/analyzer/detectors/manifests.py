"""Shared helpers for parsing dependency manifests."""

from __future__ import annotations

import re
from typing import Any

SPECIFIER_RE = re.compile(r"^[A-Za-z0-9_.\-\[\]]+")
_VERSION_OPERATOR_RE = re.compile(r"(===|==|>=|<=|~=|!=|>|<|\^|~)")

_FRAMEWORK_HINTS: dict[str, tuple[str, str]] = {
    # name -> (technology name, kind)
    "fastapi": ("FastAPI", "framework"),
    "django": ("Django", "framework"),
    "flask": ("Flask", "framework"),
    "starlette": ("Starlette", "framework"),
    "litestar": ("Litestar", "framework"),
    "aiohttp": ("aiohttp", "framework"),
    "tornado": ("Tornado", "framework"),
    "sanic": ("Sanic", "framework"),
    "uvicorn": ("Uvicorn", "web_server"),
    "gunicorn": ("Gunicorn", "web_server"),
    "hypercorn": ("Hypercorn", "web_server"),
    "pydantic": ("Pydantic", "library"),
    "sqlalchemy": ("SQLAlchemy", "library"),
    "alembic": ("Alembic", "database_tool"),
    "celery": ("Celery", "library"),
    "redis": ("Redis", "cache"),
    "pymongo": ("MongoDB", "database"),
    "psycopg": ("PostgreSQL", "database"),
    "psycopg2": ("PostgreSQL", "database"),
    "psycopg2-binary": ("PostgreSQL", "database"),
    "asyncpg": ("PostgreSQL", "database"),
    "aiosqlite": ("SQLite", "database"),
    "pytest": ("pytest", "test_framework"),
    "ruff": ("Ruff", "tool"),
    "mypy": ("mypy", "tool"),
    "black": ("Black", "tool"),
    "httpx": ("httpx", "library"),
    "typer": ("Typer", "library"),
    "click": ("Click", "library"),
}

_DATABASE_ENGINE_HINTS: dict[str, str] = {
    "psycopg": "postgresql",
    "psycopg2": "postgresql",
    "psycopg2-binary": "postgresql",
    "asyncpg": "postgresql",
    "sqlalchemy": "",
    "pymysql": "mysql",
    "mysqlclient": "mysql",
    "aiomysql": "mysql",
    "aiosqlite": "sqlite",
    "pymongo": "mongodb",
    "motor": "mongodb",
    "redis": "redis",
    "pyodbc": "mssql",
    "cx_oracle": "oracle",
    "clickhouse-connect": "clickhouse",
    "elasticsearch": "elasticsearch",
}


def normalise_name(raw: str) -> str:
    """Normalise a dependency name (lowercase, PEP 503 style)."""
    return raw.strip().strip("\"'").lower()


def split_specifier(value: str) -> tuple[str, str | None]:
    """Split ``"fastapi>=0.110"`` into ``("fastapi", ">=0.110")``."""
    cleaned = value.strip()
    if not cleaned:
        return "", None
    match = SPECIFIER_RE.match(cleaned)
    if not match:
        return cleaned, None
    name = match.group(0)
    remainder = cleaned[len(name) :].strip()
    if remainder.startswith("["):  # extras
        closing = remainder.find("]")
        if closing != -1:
            remainder = remainder[closing + 1 :].strip()
    version = remainder or None
    if version:
        version = version.split(";", 1)[0].strip() or None  # drop environment markers
    return name, version


def parse_requirements_text(text: str) -> list[tuple[str, str | None]]:
    """Parse a ``requirements.txt``-style document.

    Only name/specifier pairs are returned; ``-r`` includes, options, URLs and
    comments are skipped (an include is followed by the caller if desired).
    """
    entries: list[tuple[str, str | None]] = []
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith(("-", "--", "git+", "http://", "https://")):
            continue
        if line.endswith("\\"):
            line = line[:-1].strip()
        if ";" in line:
            line = line.split(";", 1)[0].strip()
        if "==" in line or ">=" in line or "~=" in line or line.count(" "):
            name, version = split_specifier(line.replace(" ", ""))
        else:
            name, version = split_specifier(line)
        if name:
            entries.append((name, version))
    return entries


def framework_hint(name: str) -> tuple[str, str] | None:
    """Return ``(display_name, kind)`` for a known library, else ``None``."""
    return _FRAMEWORK_HINTS.get(normalise_name(name))


def database_engine_hint(name: str) -> str | None:
    """Map a driver/ORM package to a database engine name, if known."""
    engine = _DATABASE_ENGINE_HINTS.get(normalise_name(name))
    return engine or None


def own_version(value: Any) -> str | None:
    """Extract a human version string from a TOML ``requires-python``-style value."""
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        version = value.get("version")
        return str(version).strip() if version else None
    return None
