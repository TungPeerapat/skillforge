"""Java lockfile parser: gradle.lockfile."""

from __future__ import annotations

from .base import LockedEntry, ParsedLockfile, basename, locked


class GradleLockParser:
    format = "gradle.lockfile"
    ecosystem = "maven"

    def matches(self, path: str) -> bool:
        return basename(path) == "gradle.lockfile"

    def parse(self, text: str, *, source: str) -> ParsedLockfile:
        entries: list[LockedEntry] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("empty="):
                continue
            coordinate = stripped.split("=", 1)[0]
            name, separator, remainder = coordinate.partition(":")
            artifact, second_separator, version = remainder.rpartition(":")
            if not separator or not second_separator or not name or not version:
                continue
            entry = locked("maven", f"{name}:{artifact}", version, source)
            if entry is not None:
                entries.append(entry)
        return ParsedLockfile(source, self.format, self.ecosystem, tuple(entries))
