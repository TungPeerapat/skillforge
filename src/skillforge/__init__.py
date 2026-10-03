"""SkillForge: turn your codebase and developer workflows into reusable AI agent skills."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _distribution_version

#: Single source of truth is the installed distribution metadata, which comes
#: from ``pyproject.toml``. A raw source checkout that was never installed falls
#: back to a development version instead of crashing.
try:
    __version__ = _distribution_version("skillforge")
except PackageNotFoundError:  # pragma: no cover - source checkout without install
    __version__ = "0.0.0.dev0"

__all__ = ["__version__"]
