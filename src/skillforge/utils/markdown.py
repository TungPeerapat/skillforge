"""Markdown parsing helpers (frontmatter, code blocks, links, headings).

These are intentionally small and regex based. A full Markdown parser is not
needed for the operations SkillForge performs, and this keeps the dependency
surface minimal. The interfaces are stable, so a CommonMark implementation can
replace the internals later without touching call sites.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import yaml

_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_FENCE_RE = re.compile(r"^(?P<indent>[ ]{0,3})(?P<fence>`{3,}|~{3,})[ \t]*(?P<lang>[^\s`]*)")
_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_HEADING_RE = re.compile(r"^(?P<level>#{1,6})\s+(?P<title>.+?)\s*#*\s*$")
_INLINE_PATH_RE = re.compile(r"`([^`\n]+)`")

# Files/folders that may legitimately be referenced from a SKILL.md body.
REFERENCE_ROOT_PREFIXES = ("references/", "scripts/", "assets/")


@dataclass(frozen=True)
class CodeBlock:
    """A fenced code block with its 1-based start line in the source text."""

    lang: str
    content: str
    line: int


@dataclass(frozen=True)
class FrontmatterParse:
    """Result of splitting YAML frontmatter from a Markdown document."""

    metadata: dict[str, Any] | None
    body: str
    error: str | None = None
    present: bool = False


def split_frontmatter(text: str) -> FrontmatterParse:
    """Split ``---`` YAML frontmatter from the Markdown body."""
    if not text.lstrip("\ufeff").startswith("---"):
        return FrontmatterParse(metadata=None, body=text, present=False)
    normalized = text.lstrip("\ufeff")
    match = _FRONTMATTER_RE.match(normalized)
    if not match:
        return FrontmatterParse(
            metadata=None,
            body=normalized,
            error="frontmatter block is not terminated with '---'",
            present=True,
        )
    raw = match.group(1)
    body = normalized[match.end() :]
    try:
        loaded = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        return FrontmatterParse(
            metadata=None, body=body, error=f"invalid YAML frontmatter: {exc}", present=True
        )
    if loaded is None:
        return FrontmatterParse(metadata={}, body=body, present=True)
    if not isinstance(loaded, dict):
        return FrontmatterParse(
            metadata=None,
            body=body,
            error="frontmatter must be a YAML mapping",
            present=True,
        )
    return FrontmatterParse(metadata=loaded, body=body, present=True)


# Deterministic frontmatter key order for generated skills.
_FRONTMATTER_KEY_ORDER = ("name", "description", "license", "compatibility", "allowed-tools")


def render_frontmatter(metadata: Mapping[str, Any]) -> str:
    """Render frontmatter deterministically (stable key order, sorted extras)."""

    def _sorted_nested(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {key: _sorted_nested(value[key]) for key in sorted(value)}
        return value

    ordered: dict[str, Any] = {}
    for key in _FRONTMATTER_KEY_ORDER:
        if key in metadata and metadata[key] not in (None, "", []):
            ordered[key] = _sorted_nested(metadata[key])
    for key in sorted(k for k in metadata if k not in ordered):
        value = metadata[key]
        if value not in (None, "", []):
            ordered[key] = _sorted_nested(value)
    dumped = yaml.safe_dump(
        ordered,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=100,
        line_break="\n",
    )
    return f"---\n{dumped}---\n"


def fenced_code_blocks(text: str) -> list[CodeBlock]:
    """Extract fenced code blocks (```` ``` ```` and ``~~~``)."""
    blocks: list[CodeBlock] = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        match = _FENCE_RE.match(lines[index])
        if not match:
            index += 1
            continue
        fence = match.group("fence")
        marker = fence[0]
        lang = match.group("lang")
        start_line = index + 1
        collected: list[str] = []
        index += 1
        while index < len(lines):
            candidate = lines[index]
            if candidate.strip().startswith(marker * len(fence)) and not candidate.strip().lstrip(
                marker
            ):
                break
            collected.append(candidate)
            index += 1
        blocks.append(CodeBlock(lang=lang.lower(), content="\n".join(collected), line=start_line))
        index += 1
    return blocks


def markdown_links(text: str) -> list[str]:
    """Return raw link targets in document order (duplicates preserved)."""
    return [match.group(1) for match in _LINK_RE.finditer(text)]


def headings(text: str) -> list[tuple[int, str, int]]:
    """Return ``(level, title, line)`` for every Markdown heading."""
    found: list[tuple[int, str, int]] = []
    in_fence = False
    fence_marker = ""
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            marker = stripped[:3]
            if not in_fence:
                in_fence, fence_marker = True, marker
            elif marker == fence_marker:
                in_fence = False
            continue
        if in_fence:
            continue
        match = _HEADING_RE.match(line)
        if match:
            found.append((len(match.group("level")), match.group("title").strip(), number))
    return found


def referenced_skill_paths(text: str) -> set[str]:
    """Collect skill-relative paths mentioned in links or inline code.

    Only paths that live under ``references/``, ``scripts/`` or ``assets/`` are
    returned, since those are the only internal resources a generated skill may
    reference.
    """
    candidates: set[str] = set()
    for target in markdown_links(text):
        candidates.add(target)
    for match in _INLINE_PATH_RE.finditer(text):
        candidates.add(match.group(1))
    result: set[str] = set()
    for candidate in candidates:
        cleaned = candidate.strip().strip("\"'").split("#", 1)[0].split("?", 1)[0]
        cleaned = cleaned.lstrip("./")
        if cleaned.startswith(REFERENCE_ROOT_PREFIXES):
            result.add(cleaned)
    return result


@dataclass
class LineIndex:
    """Helper for turning a character offset into a 1-based line number."""

    text: str
    _starts: list[int] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        self._starts = [0]
        for match in re.finditer("\n", self.text):
            self._starts.append(match.end())

    def line_of(self, offset: int) -> int:
        import bisect

        return bisect.bisect_right(self._starts, offset)
