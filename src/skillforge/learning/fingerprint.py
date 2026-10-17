"""Repository fingerprints and staleness detection.

A verified skill records the smallest repository state that can invalidate its
verification: the hashes of the files its workflow was derived from, the git
commit, and a fingerprint of the workflow itself. When any of those change, the
skill is reported as STALE instead of silently claiming current verification.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from skillforge.models import ExecutionStep, RepositoryFingerprint

#: Files that commonly define how a project runs. Recorded when they exist.
WORKFLOW_FILE_NAMES: tuple[str, ...] = (
    "pyproject.toml",
    "package.json",
    "Makefile",
    "makefile",
    "GNUmakefile",
    "compose.yaml",
    "compose.yml",
    "docker-compose.yml",
    "docker-compose.yaml",
    "Dockerfile",
    "Procfile",
    "Taskfile.yml",
    "Taskfile.yaml",
    "Justfile",
    "justfile",
    "go.mod",
    "Cargo.toml",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "pubspec.yaml",
    "alembic.ini",
    ".gitlab-ci.yml",
)

#: Glob patterns for CI workflows and lockfiles are not used: CI is excluded
#: from project-runner, and lockfiles are too noisy to pin. Only files the plan
#: evidence actually referenced are added on top of the fixed list.
MAX_FINGERPRINT_FILES = 64

_EXCLUDED_PREFIXES = (".git/", ".skills/", ".skillforge/", ".claude/", ".agents/", ".opencode/")


def sha256_file(path: Path) -> str | None:
    """Hash a regular file; ``None`` when unreadable or not a file."""
    try:
        if not path.is_file() or path.is_symlink():
            return None
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:  # pragma: no cover - unreadable file
        return None


def workflow_fingerprint(steps: list[ExecutionStep]) -> str:
    """Stable hash of the executable workflow (phase + command + cwd)."""
    lines = [
        f"{step.phase.value}:{step.cwd}:{step.command}"
        for step in steps
        if not step.excluded and step.command
    ]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _candidate_paths(repo_root: Path, steps: list[ExecutionStep]) -> list[str]:
    candidates: set[str] = set()
    for name in WORKFLOW_FILE_NAMES:
        if (repo_root / name).is_file():
            candidates.add(name)
    for step in steps:
        for evidence in step.evidence:
            source = evidence.source.strip().replace("\\", "/")
            if not source or source.startswith("<") or source.startswith("/"):
                continue
            if any(source.startswith(prefix) for prefix in _EXCLUDED_PREFIXES):
                continue
            if ".." in Path(source).parts:
                continue
            if (repo_root / source).is_file():
                candidates.add(source)
    return sorted(candidates)[:MAX_FINGERPRINT_FILES]


def repository_fingerprint(
    repo_root: Path,
    steps: list[ExecutionStep],
    *,
    git_commit: str | None = None,
    git_dirty: bool | None = None,
) -> RepositoryFingerprint:
    """Capture the repository state a verification depends on."""
    hashes: dict[str, str] = {}
    for relative in _candidate_paths(repo_root, steps):
        digest = sha256_file(repo_root / relative)
        if digest is not None:
            hashes[relative] = digest
    return RepositoryFingerprint(
        git_commit=git_commit,
        git_dirty=git_dirty,
        file_hashes=hashes,
        workflow_fingerprint=workflow_fingerprint(steps),
    )


@dataclass
class StalenessReport:
    """Why a verified skill is (or is not) still current."""

    stale: bool = False
    checked_files: int = 0
    reasons: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if not self.stale:
            return "current"
        return "; ".join(self.reasons[:3])


def check_staleness(
    fingerprint: RepositoryFingerprint | None,
    repo_root: Path,
    *,
    current_commit: str | None = None,
    current_workflow: str | None = None,
    current_dirty: bool | None = None,
) -> StalenessReport:
    """Compare a recorded fingerprint against the repository as it is now."""
    report = StalenessReport()
    if fingerprint is None:
        report.stale = True
        report.reasons.append("no repository fingerprint was recorded")
        return report
    for relative, recorded in sorted(fingerprint.file_hashes.items()):
        report.checked_files += 1
        digest = sha256_file(repo_root / relative)
        if digest is None:
            report.stale = True
            report.reasons.append(f"evidence file removed: {relative}")
        elif digest != recorded:
            report.stale = True
            report.reasons.append(f"evidence file changed: {relative}")
    if (
        fingerprint.git_commit
        and current_commit
        and fingerprint.git_commit != current_commit
        and fingerprint.git_dirty is False
        and current_dirty is False
    ):
        report.stale = True
        report.reasons.append(f"git commit changed: {fingerprint.git_commit} -> {current_commit}")
    if (
        current_workflow is not None
        and fingerprint.workflow_fingerprint
        and current_workflow != fingerprint.workflow_fingerprint
    ):
        report.stale = True
        report.reasons.append("the execution workflow changed")
    return report


__all__ = [
    "MAX_FINGERPRINT_FILES",
    "WORKFLOW_FILE_NAMES",
    "StalenessReport",
    "check_staleness",
    "repository_fingerprint",
    "sha256_file",
    "workflow_fingerprint",
]
