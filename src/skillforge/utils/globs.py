"""Small path-glob helpers.

``fnmatch`` treats ``*`` as matching ``/``, which makes it unsuitable for
workspace patterns such as ``packages/*``. These helpers implement the subset
of glob syntax needed for repository paths.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Final

_SPECIAL: Final[dict[str, str]] = {
    "**": ".*",
    "*": "[^/]*",
    "?": "[^/]",
}


@lru_cache(maxsize=256)
def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Compile a path glob to an anchored regex."""
    cleaned = pattern.strip().strip("/")
    parts: list[str] = []
    index = 0
    while index < len(cleaned):
        if cleaned.startswith("**/", index):
            parts.append("(?:.*/)?")
            index += 3
            continue
        if cleaned.startswith("**", index):
            parts.append(".*")
            index += 2
            continue
        char = cleaned[index]
        if char in ("*", "?"):
            parts.append(_SPECIAL[char])
        elif char == "[":
            closing = cleaned.find("]", index + 1)
            if closing == -1:
                parts.append(re.escape(char))
            else:
                inner = cleaned[index + 1 : closing]
                if inner.startswith("!"):
                    inner = "^" + inner[1:]
                parts.append(f"[{inner}]")
                index = closing + 1
                continue
        else:
            parts.append(re.escape(char))
        index += 1
    return re.compile("^" + "".join(parts) + "$")


def match_path(pattern: str, path: str) -> bool:
    """True when ``path`` matches ``pattern`` (forward slashes, case-sensitive)."""
    return bool(glob_to_regex(pattern).match(path.strip("/")))


def matches_any(patterns: tuple[str, ...] | list[str], path: str) -> bool:
    return any(match_path(pattern, path) for pattern in patterns)
