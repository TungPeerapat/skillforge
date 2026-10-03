"""Path safety: traversal, absolute-path, and symlink-escape protection.

Every write performed by SkillForge goes through :func:`safe_join`. Repositories
are untrusted input, so paths derived from repository content (component names,
CI job names, …) must be validated before they are used to build output paths.
"""

from __future__ import annotations

import os
import posixpath
import re
from pathlib import Path, PurePosixPath, PureWindowsPath

_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_UNC_RE = re.compile(r"^(?:\\\\|//)[^\\/]+[\\/]")


class PathViolation(ValueError):
    """Raised when a path could escape its root or is otherwise unsafe."""


def assert_safe_relative(relative: str, *, allow_empty: bool = False) -> str:
    """Validate a repository-relative path and return it in POSIX form.

    Rejects absolute paths, drive letters, UNC paths, NUL bytes, and any path
    that still escapes upwards after normalization.
    """
    if relative is None:
        raise PathViolation("path must not be None")
    candidate = relative.strip().replace("\\", "/")
    if not candidate:
        if allow_empty:
            return "."
        raise PathViolation("empty path is not allowed")
    if "\x00" in candidate:
        raise PathViolation("path contains a NUL byte")
    if _WINDOWS_DRIVE_RE.match(candidate) or candidate.startswith("~"):
        raise PathViolation(f"absolute or home-relative path is not allowed: {relative!r}")
    if _UNC_RE.match(relative) or PureWindowsPath(relative).is_absolute():
        raise PathViolation(f"UNC or absolute path is not allowed: {relative!r}")
    if PurePosixPath(candidate).is_absolute():
        raise PathViolation(f"absolute path is not allowed: {relative!r}")
    normalized = posixpath.normpath(candidate)
    if normalized in ("..", "."):
        if normalized == "." and allow_empty:
            return "."
        raise PathViolation(f"path traversal is not allowed: {relative!r}")
    if normalized.startswith("../"):
        raise PathViolation(f"path traversal is not allowed: {relative!r}")
    return normalized


def safe_relative(path: Path, root: Path) -> str:
    """Return ``path`` relative to ``root`` or raise :class:`PathViolation`."""
    try:
        resolved_root = root.resolve()
        resolved = path.resolve()
    except OSError as exc:  # pragma: no cover - platform specific
        raise PathViolation(f"cannot resolve path: {exc}") from exc
    if not _is_within(resolved, resolved_root):
        raise PathViolation(f"{path} is outside {root}")
    return Path(os.path.relpath(resolved, resolved_root)).as_posix()


def safe_join(root: Path, relative: str, *, must_exist_parent: bool = False) -> Path:
    """Join ``relative`` onto ``root`` with traversal and symlink checks.

    The check is performed on the **resolved** path so that a symlink inside the
    repository cannot redirect a write outside the repository.
    """
    cleaned = assert_safe_relative(relative, allow_empty=True)
    root_resolved = root.resolve()
    candidate = (root_resolved / cleaned.replace("/", os.sep)).resolve()
    if not _is_within(candidate, root_resolved):
        raise PathViolation(f"resolved path escapes root: {relative!r}")
    if must_exist_parent and not candidate.parent.exists():
        raise PathViolation(f"parent directory does not exist for: {relative!r}")
    # Reject symlinks along the chain only when they leave the root; the
    # resolve() call above already collapsed them, so compare again.
    if not _is_within(candidate, root_resolved):
        raise PathViolation(f"symlink escape detected for: {relative!r}")
    return candidate


def is_within(child: Path, parent: Path) -> bool:
    """Case-aware containment check (use with already-resolved paths)."""
    return _is_within(child.resolve(), parent.resolve())


def _is_within(child: Path, parent: Path) -> bool:
    try:
        normalized_child = os.path.normcase(str(child))
        normalized_parent = os.path.normcase(str(parent))
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return False
    if normalized_child == normalized_parent:
        return True
    prefix = normalized_parent.rstrip("\\/") + os.sep
    return normalized_child.startswith(prefix)


def is_absolute_local_path(text: str) -> bool:
    """Heuristic: does ``text`` contain an absolute local path?

    Used by the validator to reject machine-specific paths in generated skills.
    """
    if _WINDOWS_DRIVE_RE.search(text):
        return True
    if _UNC_RE.search(text):
        return True
    for marker in ("/home/", "/Users/", "/root/", "/tmp/", "/var/folders/", "/mnt/"):
        if marker in text:
            return True
    return False
