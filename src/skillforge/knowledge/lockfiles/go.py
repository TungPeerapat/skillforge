"""Go lockfile parser: go.sum (``go.mod`` requires are manifest pins, not lockfile data)."""

from __future__ import annotations

from .base import LockedEntry, ParsedLockfile, basename, locked


class GoSumParser:
    format = "go.sum"
    ecosystem = "go"

    def matches(self, path: str) -> bool:
        return basename(path) == "go.sum"

    def parse(self, text: str, *, source: str) -> ParsedLockfile:
        entries: list[LockedEntry] = []
        for line in text.splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            name, version = parts[0], parts[1]
            if version.endswith("/go.mod"):
                version = version[: -len("/go.mod")]
            entry = locked("go", name, version, source)
            if entry is not None:
                entries.append(entry)
        return ParsedLockfile(source, self.format, self.ecosystem, tuple(entries))
