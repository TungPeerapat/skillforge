"""Redis client used for caching item lookups."""

from __future__ import annotations

import redis

from app.settings import load_settings

_client: "redis.Redis[str] | None" = None


def client() -> "redis.Redis[str]":
    global _client
    if _client is None:
        _client = redis.Redis.from_url(load_settings().redis_url, decode_responses=True)
    return _client
