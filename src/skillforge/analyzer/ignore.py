"""Ignore rules: default excludes, secret files, and gitignore-compatible matching.

The matcher implements the practical subset of ``.gitignore`` semantics that
matters for repository analysis: comments, negation, anchoring, ``**``, ``*``,
``?``, character classes, and directory-only patterns. Nested ``.gitignore``
files are honoured because the scanner loads them per directory.

Files named ``.skillforgeignore`` use the same syntax and are applied first.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from skillforge.security.secrets import (
    SAFE_TEMPLATE_SUFFIXES,
    SECRET_FILE_NAMES,
    SECRET_FILE_SUFFIXES,
)

#: Directory names that are never traversed (vendor output, caches, VCS).
DEFAULT_IGNORED_DIRS: Final[frozenset[str]] = frozenset(
    {
        # VCS
        ".git",
        ".hg",
        ".svn",
        ".bzr",
        # dependency and build output
        "node_modules",
        "bower_components",
        "vendor",
        "vendors",
        "third_party",
        "site-packages",
        "dist",
        "build",
        "out",
        "target",
        "obj",
        # web framework output
        ".next",
        ".nuxt",
        ".output",
        ".svelte-kit",
        ".angular",
        ".dart_tool",
        ".expo",
        ".parcel-cache",
        ".turbo",
        ".vercel",
        ".netlify",
        # caches
        "__pycache__",
        ".venv",
        "venv",
        "env",
        ".env.d",
        ".tox",
        ".nox",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".pytype",
        ".cache",
        ".pytest_tmp",
        ".ipynb_checkpoints",
        ".terraform",
        ".serverless",
        ".gradle",
        ".m2",
        ".pnpm-store",
        ".yarn",
        ".npm",
        ".bundle",
        "Pods",
        "DerivedData",
        ".vs",
        "TestResults",
        "coverage",
        "htmlcov",
        ".nyc_output",
        "tmp",
        "temp",
        ".sass-cache",
        ".idea",
        ".vscode",
        ".DS_Store",
    }
)

#: File names that are never read (but may be recorded as present).
DEFAULT_IGNORED_FILES: Final[frozenset[str]] = frozenset(
    {".DS_Store", "Thumbs.db", "desktop.ini", ".gitkeep"}
)

#: Control files that configure tooling but carry no analysable content.
#: (``.editorconfig`` is intentionally not listed: detectors use it.)
CONTROL_FILE_NAMES: Final[frozenset[str]] = frozenset(
    {
        ".gitignore",
        ".skillforgeignore",
        ".npmignore",
        ".eslintignore",
        ".prettierignore",
        ".dockerignore",
        ".gitattributes",
        ".gitmodules",
    }
)


@dataclass(frozen=True)
class IgnorePattern:
    """A compiled ignore pattern, relative to the directory that declared it."""

    original: str
    source: str
    base: str
    regex: re.Pattern[str]
    negated: bool = False
    directory_only: bool = False

    def matches(self, rel_path: str, *, is_dir: bool) -> bool:
        if self.directory_only and not is_dir:
            return False
        if self.base and not (rel_path == self.base or rel_path.startswith(f"{self.base}/")):
            return False
        local = rel_path[len(self.base) :].lstrip("/") if self.base else rel_path
        if not local:
            return False
        return self.regex.match(local) is not None


def _translate_segment(pattern: str) -> str:
    """Translate one gitignore pattern into a regex fragment."""
    result: list[str] = []
    index = 0
    length = len(pattern)
    while index < length:
        char = pattern[index]
        if char == "*":
            if pattern.startswith("**", index):
                after = index + 2
                if after < length and pattern[after] == "/":
                    result.append("(?:.*/)?")
                    index = after + 1
                    continue
                result.append(".*")
                index = after
                continue
            result.append("[^/]*")
            index += 1
        elif char == "?":
            result.append("[^/]")
            index += 1
        elif char == "[":
            closing = pattern.find("]", index + 1)
            if closing == -1:
                result.append(re.escape(char))
                index += 1
                continue
            inner = pattern[index + 1 : closing]
            if inner.startswith("!"):
                inner = "^" + inner[1:]
            inner = inner.replace("\\", "\\\\")
            result.append(f"[{inner}]")
            index = closing + 1
        else:
            result.append(re.escape(char))
            index += 1
    return "".join(result)


def compile_pattern(
    line: str, *, source: str, base: str = "", negated: bool = False
) -> IgnorePattern | None:
    """Compile one ``.gitignore`` line, returning ``None`` for blanks/comments."""
    raw = line.rstrip("\n")
    if not raw.strip() or raw.lstrip().startswith("#"):
        return None
    if raw.startswith("!"):
        return compile_pattern(raw[1:], source=source, base=base, negated=True)
    if raw.startswith("\\#") or raw.startswith("\\!"):
        raw = raw[1:]
    directory_only = raw.endswith("/")
    if directory_only:
        raw = raw[:-1]
    anchored = raw.startswith("/")
    if anchored:
        raw = raw[1:]
    has_inner_slash = "/" in raw
    fragment = _translate_segment(raw)
    if anchored or has_inner_slash:
        regex = re.compile(f"^{fragment}(?:/.*)?$")
    else:
        regex = re.compile(f"(?:^|/){fragment}(?:/.*)?$")
    return IgnorePattern(
        original=line.strip(),
        source=source,
        base=base,
        regex=regex,
        negated=negated,
        directory_only=directory_only,
    )


def parse_gitignore(text: str, *, source: str, base: str = "") -> list[IgnorePattern]:
    """Parse a ``.gitignore``-style document into patterns."""
    patterns: list[IgnorePattern] = []
    for line in text.splitlines():
        compiled = compile_pattern(line, source=source, base=base)
        if compiled is not None:
            patterns.append(compiled)
    return patterns


class IgnoreMatcher:
    """Ordered collection of ignore patterns with negation support."""

    def __init__(self, patterns: list[IgnorePattern] | None = None) -> None:
        self._patterns: list[IgnorePattern] = list(patterns or [])

    def extend(self, patterns: list[IgnorePattern]) -> None:
        self._patterns.extend(patterns)

    def __len__(self) -> int:
        return len(self._patterns)

    @property
    def patterns(self) -> list[IgnorePattern]:
        return list(self._patterns)

    def is_ignored(self, rel_path: str, *, is_dir: bool) -> bool:
        """Return True when a path is ignored by any pattern (last match wins)."""
        ignored = False
        for pattern in self._patterns:
            for candidate, candidate_is_dir in self._candidates(rel_path, is_dir=is_dir):
                if pattern.matches(candidate, is_dir=candidate_is_dir):
                    ignored = not pattern.negated
                    break
        return ignored

    @staticmethod
    def _candidates(rel_path: str, *, is_dir: bool) -> Iterator[tuple[str, bool]]:
        parts = rel_path.split("/")
        # Ancestors are always directories.
        for index in range(1, len(parts)):
            yield "/".join(parts[:index]), True
        yield rel_path, is_dir


def is_secret_file(name: str) -> bool:
    """True when a file must never have its contents read.

    Template files (``.env.example``, ``*.sample`` …) are explicitly allowed:
    they exist to be committed and are analysed for variable *names* only.
    """
    lowered = name.lower()
    if lowered.endswith(SAFE_TEMPLATE_SUFFIXES):
        return False
    if lowered in SECRET_FILE_NAMES or lowered.startswith(".env"):
        return True
    if any(lowered.endswith(suffix) for suffix in SECRET_FILE_SUFFIXES):
        return True
    return lowered.endswith((".sqlite", ".sqlite3", ".db"))


def load_default_patterns(root: Path) -> list[IgnorePattern]:
    """Read the root ``.skillforgeignore``/``.gitignore`` if present."""
    patterns: list[IgnorePattern] = []
    for name in (".skillforgeignore", ".gitignore"):
        candidate = root / name
        if not candidate.is_file():
            continue
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:  # pragma: no cover - unreadable ignore file
            continue
        patterns.extend(parse_gitignore(text, source=name))
    return patterns


def is_ignored_directory_name(name: str) -> bool:
    return name in DEFAULT_IGNORED_DIRS
