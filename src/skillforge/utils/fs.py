"""Filesystem helpers with conservative, cross-platform behavior."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from skillforge.errors import EnvironmentError_
from skillforge.utils.text import printable_ratio

_BINARY_SNIFF_BYTES = 8192


def read_text_capped(path: Path, max_bytes: int) -> tuple[str, bool]:
    """Read a UTF-8 text file, decoding defensively.

    Returns ``(text, truncated)``. Invalid bytes are replaced rather than
    raising, so one bad file can never abort an analysis run.
    """
    with path.open("rb") as handle:
        raw = handle.read(max_bytes + 1)
    truncated = len(raw) > max_bytes
    if truncated:
        raw = raw[:max_bytes]
    text = raw.decode("utf-8", errors="replace")
    # Normalise newlines so analysis is identical on Windows and POSIX.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if truncated:
        # Do not leave a torn multi-byte character or half a line at the end.
        text = text.rsplit("\n", 1)[0]
    return text, truncated


def is_probably_binary(path: Path, *, sniff_bytes: int = _BINARY_SNIFF_BYTES) -> bool:
    """Heuristically decide whether a file is binary.

    A NUL byte is treated as decisive; otherwise a file is binary when less
    than 90% of its sampled bytes are printable text.
    """
    try:
        with path.open("rb") as handle:
            sample = handle.read(sniff_bytes)
    except OSError:
        return True
    if not sample:
        return False
    if b"\x00" in sample:
        return True
    return printable_ratio(sample) < 0.90


def atomic_write_text(path: Path, content: str, *, newline: str = "\n") -> None:
    """Write text atomically so a crash cannot leave a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = content.replace("\r\n", "\n")
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline=newline) as handle:
            handle.write(normalized)
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def relative_posix(path: Path, root: Path) -> str:
    """Return ``path`` relative to ``root`` using forward slashes."""
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:  # pragma: no cover - callers guard against this
        rel = Path(os.path.relpath(path, root))
    return rel.as_posix()


def sha256_text(text: str) -> str:
    """Stable content fingerprint used in manifests and dedupe checks."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def human_bytes(size: int) -> str:
    """Format a byte count for humans."""
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"  # pragma: no cover - unreachable


def ensure_directory(path: Path) -> Path:
    """Create ``path`` (and parents) or raise a friendly error."""
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise EnvironmentError_(f"Cannot create directory: {path}", hint=str(exc)) from exc
    if not path.is_dir():
        raise EnvironmentError_(f"Not a directory: {path}")
    return path


def read_json(path: Path, *, max_bytes: int = 8_000_000) -> object:
    """Read and parse a JSON file with a size guard."""
    import json

    from skillforge.errors import SkillForgeError

    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SkillForgeError(f"Cannot read {path}", hint=str(exc)) from exc
    if len(raw) > max_bytes:
        raise SkillForgeError(f"Refusing to parse oversized JSON file: {path}")
    try:
        return json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise SkillForgeError(f"Invalid JSON in {path}: {exc}") from exc
