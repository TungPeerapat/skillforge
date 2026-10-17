"""Python lockfile parsers: uv.lock, poetry.lock, pdm.lock, Pipfile.lock."""

from __future__ import annotations

import json
from typing import Any

from skillforge.utils.toml import load_toml_text

from .base import LockedEntry, ParsedLockfile, basename, locked

#: ``source`` markers for the project itself (not a dependency).
_ROOT_SOURCE_KEYS = frozenset({"editable", "virtual", "directory", "path", "executable"})


def _toml_packages(text: str, source: str) -> list[LockedEntry]:
    data = load_toml_text(text, source=source)
    packages = data.get("package")
    if not isinstance(packages, list):
        return []
    entries: list[LockedEntry] = []
    for package in packages:
        if not isinstance(package, dict):
            continue
        source_info = package.get("source")
        if isinstance(source_info, dict) and any(key in source_info for key in _ROOT_SOURCE_KEYS):
            continue
        entry = locked(
            "pypi",
            str(package.get("name") or ""),
            str(package.get("version") or ""),
            source,
        )
        if entry is not None:
            entries.append(entry)
    return entries


class UvLockParser:
    format = "uv.lock"
    ecosystem = "pypi"

    def matches(self, path: str) -> bool:
        return basename(path) in {"uv.lock", "poetry.lock", "pdm.lock"}

    def parse(self, text: str, *, source: str) -> ParsedLockfile:
        return ParsedLockfile(
            source, self.format, self.ecosystem, tuple(_toml_packages(text, source))
        )


class PipfileLockParser:
    format = "Pipfile.lock"
    ecosystem = "pypi"

    def matches(self, path: str) -> bool:
        return basename(path) == "pipfile.lock"

    def parse(self, text: str, *, source: str) -> ParsedLockfile:
        data = json.loads(text)
        entries: list[LockedEntry] = []
        if isinstance(data, dict):
            for section in ("default", "develop"):
                items: Any = data.get(section)
                if not isinstance(items, dict):
                    continue
                for name, meta in items.items():
                    if not isinstance(meta, dict):
                        continue
                    version = str(meta.get("version") or "")
                    if not version.startswith("=="):
                        continue
                    entry = locked("pypi", name, version[2:].strip(), source)
                    if entry is not None:
                        entries.append(entry)
        return ParsedLockfile(source, self.format, self.ecosystem, tuple(entries))
