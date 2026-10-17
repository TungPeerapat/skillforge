"""NuGet lockfile parsers: packages.lock.json and obj/project.assets.json."""

from __future__ import annotations

import json
from typing import Any

from .base import LockedEntry, ParsedLockfile, basename, locked


class NuGetLockParser:
    format = "packages.lock.json"
    ecosystem = "nuget"

    def matches(self, path: str) -> bool:
        lowered = path.replace("\\", "/").lower()
        return basename(path) == "packages.lock.json" or lowered.endswith("obj/project.assets.json")

    def parse(self, text: str, *, source: str) -> ParsedLockfile:
        data = json.loads(text)
        entries: list[LockedEntry] = []
        if not isinstance(data, dict):
            return ParsedLockfile(source, self.format, self.ecosystem, ())
        dependencies: Any = data.get("dependencies")
        libraries: Any = data.get("libraries")
        if isinstance(dependencies, dict):
            for framework in dependencies.values():
                if not isinstance(framework, dict):
                    continue
                for name, meta in framework.items():
                    if not isinstance(meta, dict) or meta.get("type") == "Project":
                        continue
                    entry = locked("nuget", name, str(meta.get("resolved") or ""), source)
                    if entry is not None:
                        entries.append(entry)
        elif isinstance(libraries, dict):
            for key in libraries:
                name, _, version = str(key).rpartition("/")
                entry = locked("nuget", name, version, source)
                if entry is not None:
                    entries.append(entry)
        return ParsedLockfile(source, self.format, self.ecosystem, tuple(entries))
