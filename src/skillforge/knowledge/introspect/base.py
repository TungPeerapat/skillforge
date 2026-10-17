"""Artifact introspection base types (K6).

Hard rule: **never execute dependency code** — no imports, no ``node``, no
reflection over loaded assemblies. Only text, AST, and archive metadata are
read. Evidence paths are package-relative; absolute machine paths never enter
models, the store, or compiled skills.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from skillforge.knowledge.artifacts import InstalledArtifact
from skillforge.models import ArtifactEvidence, CapabilityDraft, Certainty
from skillforge.utils.fs import read_text_capped, sha256_file


@dataclass(frozen=True, slots=True)
class Budgets:
    """Hard bounds for one artifact introspection pass."""

    max_files_per_artifact: int = 2_000
    max_file_bytes: int = 400_000
    max_symbols_per_artifact: int = 20_000
    max_depth: int = 8

    def __post_init__(self) -> None:
        if min(self.max_files_per_artifact, self.max_file_bytes, self.max_symbols_per_artifact) < 1:
            raise ValueError("artifact budgets must be positive")
        if self.max_depth < 0:
            raise ValueError("artifact max_depth cannot be negative")


@dataclass(frozen=True, slots=True)
class ArtifactLocation:
    """Resolved, on-disk location of one installed artifact."""

    ecosystem: str
    name: str
    version: str
    directory: Path
    metadata_dir: Path | None = None
    identity: str = ""


@dataclass
class IntrospectionResult:
    """Drafts found in one artifact plus honest diagnostics."""

    drafts: list[CapabilityDraft] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)
    diagnostics: list[str] = field(default_factory=list)
    accuracy: Certainty = Certainty.INFERENCE


class ArtifactIntrospector(Protocol):
    """One ecosystem's static artifact reader."""

    ecosystem: str

    def introspect(
        self,
        location: ArtifactLocation,
        *,
        technology: str,
        version: str,
        budgets: Budgets,
    ) -> IntrospectionResult: ...


def confined_path(root: Path, candidate: Path) -> bool:
    """Reject traversal, symlinks and junctions, including intermediate parents."""
    try:
        relative = candidate.absolute().relative_to(root.absolute())
        if ".." in relative.parts:
            return False
        current = root.absolute()
        for part in ("", *relative.parts):
            if part:
                current /= part
            if current.is_symlink() or current.is_junction():
                return False
        candidate.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def artifact_files(root: Path, suffix: str, budgets: Budgets) -> list[Path]:
    """Bounded-depth, non-symlink traversal; stop collecting at the file cap."""
    found: list[Path] = []
    for directory, dirs, names in os.walk(root, followlinks=False):
        depth = len(Path(directory).relative_to(root).parts)
        dirs[:] = sorted(
            name
            for name in dirs
            if depth < budgets.max_depth
            and name not in {"node_modules", "__pycache__", "vendor", "testdata"}
            and confined_path(root, Path(directory) / name)
        )
        for name in sorted(names):
            path = Path(directory) / name
            if name.endswith(suffix) and confined_path(root, path):
                found.append(path)
                if len(found) >= budgets.max_files_per_artifact:
                    return found
    return found


@lru_cache(maxsize=256)
def _file_digest(path: Path, size: int, modified_ns: int) -> str:
    """Avoid rehashing the same bounded source once per symbol."""
    return sha256_file(path)


def evidence_for(
    *,
    kind: str,
    relative: str,
    locator: str = "",
    detail: str = "",
    file_path: Path | None = None,
) -> ArtifactEvidence:
    digest = ""
    if file_path is not None and file_path.is_file():
        try:
            stat = file_path.stat()
            digest = _file_digest(file_path, stat.st_size, stat.st_mtime_ns)
        except OSError:  # pragma: no cover - unreadable file
            digest = ""
    return ArtifactEvidence(kind=kind, path=relative, locator=locator, hash=digest, detail=detail)


def locate_artifact(root: Path, artifact: InstalledArtifact) -> ArtifactLocation | None:
    """Resolve an installed artifact to its package directory (never executes)."""
    base = root / artifact.path
    if not confined_path(root, base):
        return None
    if artifact.ecosystem == "pypi":
        metadata_dir = base
        if not (metadata_dir / "METADATA").is_file():
            if base.suffix == ".dist-info" and base.is_dir():
                metadata_dir = base
            else:
                return None
        site_packages = metadata_dir.parent
        candidates: list[str] = []
        top_level = metadata_dir / "top_level.txt"
        if confined_path(root, top_level) and top_level.is_file():
            try:
                text, _ = read_text_capped(top_level, 16_384)
                first = text.splitlines()
            except OSError:  # pragma: no cover - unreadable file
                first = []
            if first and first[0].strip():
                candidates.append(first[0].strip())
        candidates.extend([artifact.name.replace("-", "_"), artifact.name.lower(), artifact.name])
        for name in candidates:
            candidate = site_packages / name
            if confined_path(root, candidate) and candidate.is_dir():
                return ArtifactLocation(
                    ecosystem="pypi",
                    name=artifact.name,
                    version=artifact.version,
                    directory=candidate,
                    metadata_dir=metadata_dir,
                )
        return None
    if base.is_dir():
        return ArtifactLocation(
            ecosystem=artifact.ecosystem,
            name=artifact.name,
            version=artifact.version,
            directory=base,
        )
    if base.is_file():
        return ArtifactLocation(
            ecosystem=artifact.ecosystem,
            name=artifact.name,
            version=artifact.version,
            directory=base,
        )
    return None


__all__ = [
    "ArtifactIntrospector",
    "ArtifactLocation",
    "Budgets",
    "IntrospectionResult",
    "evidence_for",
    "locate_artifact",
]
