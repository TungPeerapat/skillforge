"""Lockfile parser registry (K2).

``parse_lockfile`` is failure-tolerant on purpose: malformed input produces an
empty result plus a warning, so resolution can mark entries ``UNRESOLVED`` with
a recorded reason instead of crashing.
"""

from __future__ import annotations

from skillforge.analyzer.scanner import ScanResult

from .base import LockedEntry, LockfileParser, ParsedLockfile
from .dart import PubspecLockParser
from .dotnet import NuGetLockParser
from .go import GoSumParser
from .java import GradleLockParser
from .node import PackageLockParser, PnpmLockParser, YarnLockParser
from .python import PipfileLockParser, UvLockParser

_PARSERS: tuple[LockfileParser, ...] = (
    UvLockParser(),
    PipfileLockParser(),
    PackageLockParser(),
    PnpmLockParser(),
    YarnLockParser(),
    GoSumParser(),
    NuGetLockParser(),
    PubspecLockParser(),
    GradleLockParser(),
)


def parser_for(path: str) -> LockfileParser | None:
    """Return the first parser that owns ``path``, or ``None``."""
    for parser in _PARSERS:
        if parser.matches(path):
            return parser
    return None


def parse_lockfile(path: str, text: str) -> ParsedLockfile | None:
    """Parse one lockfile; malformed input becomes a warning, never a raise."""
    parser = parser_for(path)
    if parser is None:
        return None
    try:
        return parser.parse(text, source=path)
    except Exception as exc:  # untrusted structured data: warn, never raise
        return ParsedLockfile(
            path=path,
            format=parser.format,
            ecosystem=parser.ecosystem,
            entries=(),
            warnings=(f"could not parse {path} as {parser.format}: {type(exc).__name__}",),
        )


def parse_scan_lockfiles(scan: ScanResult) -> tuple[list[ParsedLockfile], list[str]]:
    """Parse every retained lockfile in a scan, in deterministic path order."""
    parsed: list[ParsedLockfile] = []
    warnings: list[str] = []
    for path in sorted(scan.contents):
        result = parse_lockfile(path, scan.contents[path])
        if result is None:
            continue
        parsed.append(result)
        warnings.extend(result.warnings)
    return parsed, warnings


__all__ = [
    "LockedEntry",
    "LockfileParser",
    "ParsedLockfile",
    "parse_lockfile",
    "parse_scan_lockfiles",
    "parser_for",
]
