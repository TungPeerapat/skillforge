"""TOML loading with friendly errors."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from skillforge.errors import ConfigError


def load_toml_text(text: str, *, source: str = "<string>") -> dict[str, Any]:
    """Parse TOML text, mapping decode errors to :class:`ConfigError`."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid TOML in {source}: {exc}") from exc
    if not isinstance(data, dict):  # pragma: no cover - tomllib always returns dict
        raise ConfigError(f"Top level of {source} must be a TOML table")
    return data


def load_toml_file(path: Path) -> dict[str, Any]:
    """Load a TOML file, raising :class:`ConfigError` on any problem."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ConfigError(f"Cannot read configuration file: {path}", hint=str(exc)) from exc
    return load_toml_text(data.decode("utf-8", errors="replace"), source=str(path))
