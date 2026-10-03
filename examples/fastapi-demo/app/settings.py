"""Runtime configuration read from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    redis_url: str


def load_settings() -> Settings:
    return Settings(
        database_url=os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:5432/app"),
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
    )
