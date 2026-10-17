"""Dart lockfile parser: pubspec.lock (hosted packages only)."""

from __future__ import annotations

from typing import Any

import yaml

from .base import LockedEntry, ParsedLockfile, basename, locked


class PubspecLockParser:
    format = "pubspec.lock"
    ecosystem = "pub"

    def matches(self, path: str) -> bool:
        return basename(path) == "pubspec.lock"

    def parse(self, text: str, *, source: str) -> ParsedLockfile:
        data = yaml.safe_load(text)
        packages: Any = data.get("packages") if isinstance(data, dict) else None
        entries: list[LockedEntry] = []
        if isinstance(packages, dict):
            for name, meta in packages.items():
                if not isinstance(meta, dict):
                    continue
                # ``git``/``path`` sources are refs, not package versions.
                if meta.get("source") not in (None, "hosted"):
                    continue
                entry = locked("pub", str(name), str(meta.get("version") or ""), source)
                if entry is not None:
                    entries.append(entry)
        return ParsedLockfile(source, self.format, self.ecosystem, tuple(entries))
