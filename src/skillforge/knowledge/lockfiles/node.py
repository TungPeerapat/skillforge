"""Node lockfile parsers: package-lock.json, pnpm-lock.yaml, yarn.lock.

``resolved``/``integrity`` fields are ignored entirely: registry URLs are never
read, fetched, or emitted.
"""

from __future__ import annotations

import json
import re
from typing import Any

import yaml

from .base import LockedEntry, ParsedLockfile, basename, locked

_NODE_MODULES_MARKER = "node_modules/"
_YARN_VERSION_RE = re.compile(r"^version[:\s]+[\"']?(?P<version>[^\"'\s]+)")
_YARN_PROTOCOL_RE = re.compile(r"@(?:npm|patch|workspace|link|file|github|git|https?):")


def _name_from_node_path(path: str) -> str:
    index = path.rfind(_NODE_MODULES_MARKER)
    if index < 0:
        return ""
    return path[index + len(_NODE_MODULES_MARKER) :].strip("/")


class PackageLockParser:
    format = "package-lock.json"
    ecosystem = "npm"

    def matches(self, path: str) -> bool:
        return basename(path) in {"package-lock.json", "npm-shrinkwrap.json"}

    def parse(self, text: str, *, source: str) -> ParsedLockfile:
        data = json.loads(text)
        entries: list[LockedEntry] = []
        packages: Any = data.get("packages") if isinstance(data, dict) else None
        if isinstance(packages, dict) and packages:
            for path, meta in packages.items():
                if not path or not isinstance(meta, dict) or meta.get("link"):
                    continue
                name = str(meta.get("name") or _name_from_node_path(str(path)))
                entry = locked("npm", name, str(meta.get("version") or ""), source)
                if entry is not None:
                    entries.append(entry)
        elif isinstance(data, dict):
            self._walk_v1(data.get("dependencies"), entries, source)
        return ParsedLockfile(source, self.format, self.ecosystem, tuple(entries))

    def _walk_v1(self, dependencies: Any, entries: list[LockedEntry], source: str) -> None:
        if not isinstance(dependencies, dict):
            return
        for name, meta in dependencies.items():
            if not isinstance(meta, dict) or meta.get("link"):
                continue
            entry = locked("npm", str(name), str(meta.get("version") or ""), source)
            if entry is not None:
                entries.append(entry)
            self._walk_v1(meta.get("dependencies"), entries, source)


def _split_pnpm_key(key: str) -> tuple[str, str] | None:
    cleaned = key.strip().split("(")[0]  # drop peer suffixes
    if cleaned.startswith("/"):  # pnpm v6: /name/1.2.3 or /@scope/name/1.2.3
        body = cleaned.strip("/")
        name, _, version = body.rpartition("/")
        if not name or not version:
            return None
    elif "@" in cleaned:  # pnpm v9: name@1.2.3 or @scope/name@1.2.3
        name, _, version = cleaned.rpartition("@")
    else:
        return None
    if not name or not version:
        return None
    if ":" in version:  # e.g. registry alias prefixes
        version = version.rsplit(":", 1)[-1]
    if not version[:1].isdigit():
        return None
    return name, version


class PnpmLockParser:
    format = "pnpm-lock.yaml"
    ecosystem = "npm"

    def matches(self, path: str) -> bool:
        return basename(path) == "pnpm-lock.yaml"

    def parse(self, text: str, *, source: str) -> ParsedLockfile:
        data = yaml.safe_load(text)
        packages = data.get("packages") if isinstance(data, dict) else None
        entries: list[LockedEntry] = []
        if isinstance(packages, dict):
            for key in packages:
                split = _split_pnpm_key(str(key))
                if split is None:
                    continue
                entry = locked("npm", split[0], split[1], source)
                if entry is not None:
                    entries.append(entry)
        return ParsedLockfile(source, self.format, self.ecosystem, tuple(entries))


def _yarn_selector_name(selector: str) -> str:
    cleaned = selector.strip().strip("\"'")
    cleaned = _YARN_PROTOCOL_RE.split(cleaned)[0]
    return cleaned.rpartition("@")[0] or cleaned


class YarnLockParser:
    format = "yarn.lock"
    ecosystem = "npm"

    def matches(self, path: str) -> bool:
        return basename(path) == "yarn.lock"

    def parse(self, text: str, *, source: str) -> ParsedLockfile:
        entries: list[LockedEntry] = []
        current: str | None = None
        for raw in text.splitlines():
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if not raw[0].isspace() and stripped.endswith(":"):
                selector = stripped[:-1].split(",")[0]
                name = _yarn_selector_name(selector)
                current = None if not name or name.startswith("__") else name
                continue
            if current and stripped.startswith("version"):
                match = _YARN_VERSION_RE.match(stripped)
                if match:
                    entry = locked("npm", current, match.group("version"), source)
                    if entry is not None:
                        entries.append(entry)
                current = None
        return ParsedLockfile(source, self.format, self.ecosystem, tuple(entries))
