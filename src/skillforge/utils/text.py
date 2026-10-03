"""Text utilities used by analysis, discovery, and rendering."""

from __future__ import annotations

import re
from collections.abc import Iterable

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_WHITESPACE_RE = re.compile(r"[ \t]+")
_COMMAND_PROMPTS = ("$ ", "> ", "PS> ", "PS C:\\> ", "# ", "% ", ">>> ")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from terminal output."""
    return _ANSI_RE.sub("", text)


def normalize_whitespace(text: str) -> str:
    """Collapse runs of spaces/tabs and trim each line."""
    lines = [_WHITESPACE_RE.sub(" ", line).strip() for line in text.splitlines()]
    return "\n".join(lines).strip()


def slugify(value: str, *, max_length: int = 64) -> str:
    """Convert arbitrary text to a lowercase hyphen-separated slug.

    The result always satisfies the Agent Skills ``name`` grammar
    (``^[a-z0-9]+(-[a-z0-9]+)*$``) when the input contains at least one
    alphanumeric character.
    """
    slug = _SLUG_RE.sub("-", value.strip().lower()).strip("-")
    if len(slug) > max_length:
        slug = slug[:max_length].rstrip("-")
    return slug


def strip_command_prefix(line: str) -> str:
    """Remove common shell prompt markers from a documented command line."""
    candidate = line.strip()
    for prompt in _COMMAND_PROMPTS:
        if candidate.startswith(prompt):
            return candidate[len(prompt) :].strip()
    return candidate


def count_nonblank_lines(text: str) -> int:
    """Count lines that contain more than whitespace."""
    return sum(1 for line in text.splitlines() if line.strip())


def truncate(text: str, max_chars: int, *, suffix: str = " [...]") -> str:
    """Truncate ``text`` to at most ``max_chars`` characters."""
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    if len(suffix) >= max_chars:
        return text[:max_chars]
    keep = max_chars - len(suffix)
    return text[:keep].rstrip() + suffix


def dedupe_preserving_order(items: Iterable[str]) -> list[str]:
    """De-duplicate strings while keeping the first occurrence order."""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def pluralize(count: int, singular: str, plural: str | None = None) -> str:
    """Return ``"1 file"`` / ``"3 files"`` style text."""
    if count == 1:
        return f"{count} {singular}"
    return f"{count} {plural or singular + 's'}"


def indent_block(text: str, prefix: str = "  ") -> str:
    """Prefix every non-empty line of ``text``."""
    return "\n".join(prefix + line if line else line for line in text.splitlines())


def bullet_list(items: Iterable[str], *, bullet: str = "-") -> str:
    """Render items as a markdown bullet list."""
    return "\n".join(f"{bullet} {item}" for item in items)


def printable_ratio(data: bytes) -> float:
    """Fraction of bytes that look like printable text (UTF-8 tolerant)."""
    if not data:
        return 1.0
    printable = sum(1 for byte in data if 32 <= byte < 127 or byte in (9, 10, 13))
    return printable / len(data)
