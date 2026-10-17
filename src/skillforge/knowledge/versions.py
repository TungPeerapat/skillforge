"""Per-ecosystem version parsing, normalization, and comparison (K2).

Design rules from the v0.3 plan:

* version strings are normalized for *comparison and conflict detection* only —
  the resolved value stored in artifacts is the version as declared by the
  source (minus leading ``v`` where the ecosystem strips it);
* a spec that permits other versions is never reported as exact;
* unknown ecosystems fall back to exact string equality and never guess.

PEP 440 handling uses ``packaging`` (approved dependency); other ecosystems use
small deterministic parsers. Range matching is used only to decide exactness,
never to invent a version.
"""

from __future__ import annotations

import re
from typing import Protocol

from packaging.version import InvalidVersion, Version

from skillforge.models.knowledge import ResolutionMethod

#: Ecosystem identifiers used across the knowledge layer.
ECOSYSTEMS = frozenset({"pypi", "npm", "nuget", "go", "pub", "maven"})

_PYPI_PIN = re.compile(r"^={1,3}[ \t]*(?P<version>[0-9][^,\s;*]*)(?:\s*;.*)?$")
_EXACT_SEMVER = re.compile(
    r"^v?(?P<version>\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?)$"
)
_NUGET_EXACT = re.compile(r"^\[(?P<version>[^\],\s]+)\]$")
_MAVEN_EXACT = re.compile(r"^(?P<version>\d+(?:\.\d+)*(?:[.-][0-9A-Za-z][0-9A-Za-z.-]*)?)$")
_GO_EXACT = re.compile(r"^(?P<version>v?\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.+-]+)?)$")


class VersionParser(Protocol):
    """Parse/normalize/compare versions for one ecosystem family."""

    ecosystem: str

    def normalize(self, version: str) -> str | None:
        """Return a canonical form, or ``None`` when the value is not a version."""
        ...

    def compare(self, left: str, right: str) -> int | None:
        """Compare two versions (``-1``/``0``/``1``) or ``None`` when incomparable."""
        ...


class Pep440VersionParser:
    """Python (PyPI) versions via ``packaging``."""

    ecosystem = "pypi"

    def normalize(self, version: str) -> str | None:
        try:
            return str(Version(version.strip()))
        except InvalidVersion:
            return None

    def compare(self, left: str, right: str) -> int | None:
        try:
            first = Version(left.strip())
            second = Version(right.strip())
        except InvalidVersion:
            return None
        if first == second:
            return 0
        return -1 if first < second else 1


class SemverVersionParser:
    """npm / Go / Dart style ``x.y.z[-pre][+build]`` versions."""

    def __init__(self, ecosystem: str) -> None:
        self.ecosystem = ecosystem

    _SEMVER = re.compile(
        r"^v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
        r"(?:-(?P<pre>[0-9A-Za-z.-]+))?(?:\+(?P<build>[0-9A-Za-z.-]+))?$"
    )

    def _parts(self, version: str) -> tuple[int, int, int, tuple[str, ...]] | None:
        match = self._SEMVER.match(version.strip())
        if match is None:
            return None
        pre = tuple((match.group("pre") or "").split(".")) if match.group("pre") else ()
        return (
            int(match.group("major")),
            int(match.group("minor")),
            int(match.group("patch")),
            pre,
        )

    @staticmethod
    def _pre_rank(parts: tuple[str, ...]) -> tuple[int, ...]:
        ranks: list[int] = []
        for part in parts:
            ranks.append(0 if part.isdigit() else 1)
            ranks.append(int(part) if part.isdigit() else 0)
            if part.isdigit():
                continue
        return tuple(ranks)

    def normalize(self, version: str) -> str | None:
        parts = self._parts(version)
        if parts is None:
            return None
        major, minor, patch, pre = parts
        base = f"{major}.{minor}.{patch}"
        return f"{base}-{'.'.join(pre)}" if pre else base

    def compare(self, left: str, right: str) -> int | None:
        first = self._parts(left)
        second = self._parts(right)
        if first is None or second is None:
            return None
        left_core, right_core = first[:3], second[:3]
        if left_core != right_core:
            return -1 if left_core < right_core else 1
        left_pre, right_pre = first[3], second[3]
        if left_pre == right_pre:
            return 0
        # A release outranks any prerelease.
        if not left_pre:
            return 1
        if not right_pre:
            return -1
        return -1 if self._pre_rank(left_pre) < self._pre_rank(right_pre) else 1


class NuGetVersionParser:
    """NuGet ``major.minor[.patch[.rev]][-pre]`` versions."""

    ecosystem = "nuget"
    _PATTERN = re.compile(r"^\d+(?:\.\d+){0,3}(?:-[0-9A-Za-z.-]+)?$")

    def normalize(self, version: str) -> str | None:
        cleaned = version.strip()
        if not self._PATTERN.match(cleaned):
            return None
        # NuGet version comparison ignores case for the prerelease label.
        return cleaned.lower()

    def compare(self, left: str, right: str) -> int | None:
        first = self.normalize(left)
        second = self.normalize(right)
        if first is None or second is None:
            return None
        if first == second:
            return 0
        first_core, _, first_pre = first.partition("-")
        second_core, _, second_pre = second.partition("-")
        first_parts = [int(part) for part in first_core.split(".")]
        second_parts = [int(part) for part in second_core.split(".")]
        width = max(len(first_parts), len(second_parts))
        first_parts += [0] * (width - len(first_parts))
        second_parts += [0] * (width - len(second_parts))
        if first_parts != second_parts:
            return -1 if first_parts < second_parts else 1
        if not first_pre:
            return 1
        if not second_pre:
            return -1
        return -1 if first_pre < second_pre else 1


class MavenVersionParser:
    """Maven versions: loose numeric comparison, exact string equality first."""

    ecosystem = "maven"
    _PATTERN = re.compile(r"^[0-9][0-9A-Za-z._-]*$")

    def normalize(self, version: str) -> str | None:
        cleaned = version.strip()
        return cleaned if self._PATTERN.match(cleaned) else None

    def compare(self, left: str, right: str) -> int | None:
        first = self.normalize(left)
        second = self.normalize(right)
        if first is None or second is None:
            return None
        if first == second:
            return 0
        first_parts = re.split(r"[._-]", first)
        second_parts = re.split(r"[._-]", second)
        for a, b in zip(first_parts, second_parts, strict=False):
            if a == b:
                continue
            if a.isdigit() and b.isdigit():
                return -1 if int(a) < int(b) else 1
            return -1 if a < b else 1
        if len(first_parts) == len(second_parts):
            return 0
        return -1 if len(first_parts) < len(second_parts) else 1


class PlainVersionParser:
    """Fallback: exact string identity, never a guess."""

    def __init__(self, ecosystem: str) -> None:
        self.ecosystem = ecosystem

    def normalize(self, version: str) -> str | None:
        cleaned = version.strip()
        return cleaned or None

    def compare(self, left: str, right: str) -> int | None:
        first = self.normalize(left)
        second = self.normalize(right)
        if first is None or second is None:
            return None
        return 0 if first == second else None


_PARSERS: dict[str, VersionParser] = {
    "pypi": Pep440VersionParser(),
    "npm": SemverVersionParser("npm"),
    "go": SemverVersionParser("go"),
    "pub": SemverVersionParser("pub"),
    "nuget": NuGetVersionParser(),
    "maven": MavenVersionParser(),
}


def parser_for(ecosystem: str) -> VersionParser:
    return _PARSERS.get(ecosystem, PlainVersionParser(ecosystem))


def normalize_version(ecosystem: str, version: str) -> str | None:
    return parser_for(ecosystem).normalize(version)


def versions_agree(ecosystem: str, left: str, right: str) -> bool:
    """True only when both parse and compare as the same version."""
    parser = parser_for(ecosystem)
    result = parser.compare(left, right)
    if result is not None:
        return result == 0
    return left.strip() == right.strip()


def detect_pin(spec: str | None, ecosystem: str) -> tuple[str | None, ResolutionMethod]:
    """Resolve an exact version from a manifest spec, or classify the range.

    Returns ``(version, method)``. Conservative by design: a spec that permits
    any other version is *not* a resolved version.
    """
    if not spec or not spec.strip():
        return None, ResolutionMethod.NONE
    cleaned = spec.strip().split(";")[0].strip()
    if ecosystem == "pypi":
        match = _PYPI_PIN.match(cleaned)
        if match:
            normalized = normalize_version("pypi", match.group("version"))
            if normalized is not None:
                return match.group("version"), ResolutionMethod.MANIFEST_PIN
            return None, ResolutionMethod.MANIFEST_RANGE
        return None, ResolutionMethod.MANIFEST_RANGE
    if ecosystem in ("npm", "pub"):
        match = _EXACT_SEMVER.match(cleaned)
        if match:
            return match.group("version"), ResolutionMethod.MANIFEST_PIN
        return None, ResolutionMethod.MANIFEST_RANGE
    if ecosystem == "go":
        match = _GO_EXACT.match(cleaned)
        if match:
            return match.group("version"), ResolutionMethod.MANIFEST_PIN
        return None, ResolutionMethod.MANIFEST_RANGE
    if ecosystem == "nuget":
        match = _NUGET_EXACT.match(cleaned)
        if match:
            return match.group("version"), ResolutionMethod.MANIFEST_PIN
        return None, ResolutionMethod.MANIFEST_RANGE
    if ecosystem == "maven":
        match = _MAVEN_EXACT.match(cleaned)
        if match:
            return match.group("version"), ResolutionMethod.MANIFEST_PIN
        return None, ResolutionMethod.MANIFEST_RANGE
    return None, ResolutionMethod.MANIFEST_RANGE


__all__ = [
    "ECOSYSTEMS",
    "MavenVersionParser",
    "NuGetVersionParser",
    "Pep440VersionParser",
    "PlainVersionParser",
    "SemverVersionParser",
    "VersionParser",
    "detect_pin",
    "normalize_version",
    "parser_for",
    "versions_agree",
]
