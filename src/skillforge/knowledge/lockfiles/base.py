"""Shared types for lockfile parsers (K2).

Lockfile text is untrusted structured data: it is parsed without importing or
executing anything, ``resolved`` URLs are never fetched or copied into
evidence, and every parser is failure-tolerant (a malformed lockfile produces a
warning, never an exception).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class LockedEntry:
    """One ``(ecosystem, name) -> version`` fact from a lockfile."""

    ecosystem: str
    name: str
    version: str
    source: str


@dataclass(frozen=True, slots=True)
class ParsedLockfile:
    """Parse result for one lockfile; warnings travel with the result."""

    path: str
    format: str
    ecosystem: str
    entries: tuple[LockedEntry, ...]
    warnings: tuple[str, ...] = ()


class LockfileParser(Protocol):
    """Detect and parse one lockfile format."""

    format: str
    ecosystem: str

    def matches(self, path: str) -> bool:
        """True when this parser owns the file at ``path`` (posix-relative)."""
        ...

    def parse(self, text: str, *, source: str) -> ParsedLockfile:
        """Parse retained lockfile text; never raise on malformed input."""
        ...


def basename(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1].lower()


def locked(ecosystem: str, name: str, version: str, source: str) -> LockedEntry | None:
    """Build an entry, dropping empties; version must start with a digit."""
    cleaned_name = name.strip()
    cleaned_version = version.strip()
    if not cleaned_name or not cleaned_version:
        return None
    if not cleaned_version[0].isdigit() and not cleaned_version.startswith("v"):
        return None
    return LockedEntry(ecosystem, cleaned_name, cleaned_version, source)


__all__ = ["LockedEntry", "LockfileParser", "ParsedLockfile", "basename", "locked"]
