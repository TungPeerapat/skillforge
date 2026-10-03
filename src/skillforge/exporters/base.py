"""Exporter base class.

Exporters only transform an already-generated skill into a target agent's
layout. They never re-generate content, and they never overwrite files outside
their own target directory.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from skillforge.errors import SkillExistsError
from skillforge.logging import get_logger
from skillforge.models import (
    MANIFEST_FILENAME,
    GeneratedSkill,
    SkillBundle,
    SkillManifest,
    SkillMetadata,
)
from skillforge.security.paths import PathViolation, safe_join
from skillforge.skills.store import StoredSkill, read_bundle
from skillforge.utils.fs import atomic_write_text

logger = get_logger("exporters")


@dataclass
class ExportResult:
    """What an export wrote."""

    exporter: str
    root: Path
    skills: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.skills)


class BaseExporter(ABC):
    """Common export logic; subclasses only decide where skills live."""

    #: Stable identifier used by the CLI (``--to``).
    id: str = ""
    #: Human-readable name.
    display_name: str = ""
    #: One-line description of where skills are written and how the agent finds them.
    description: str = ""
    #: Relative directory that holds skills, for example ``.claude/skills``.
    skills_subdir: str = ""

    def __init__(self, *, global_scope: bool = False) -> None:
        self._global_scope = global_scope

    # ------------------------------------------------------------------ layout
    @abstractmethod
    def skills_root(self, repo_root: Path) -> Path:
        """Return the directory that holds skill folders for this agent."""

    def skill_dir(self, repo_root: Path, skill_name: str) -> Path:
        return self.skills_root(repo_root) / skill_name

    # ------------------------------------------------------------------ export
    def export(
        self,
        bundle: SkillBundle,
        repo_root: Path,
        *,
        manifest: SkillManifest | None = None,
        force: bool = False,
    ) -> Path:
        """Write one skill for this agent and return its directory."""
        self._assert_supported_metadata(bundle.metadata)
        target = self.skill_dir(repo_root, bundle.metadata.name)
        if target.exists() and any(target.iterdir()) and not force:
            raise SkillExistsError(
                f"{self.display_name} skill already exists: {target}",
                hint="pass --force to overwrite",
            )
        contents = bundle.all_contents()
        if manifest is not None:
            contents[MANIFEST_FILENAME] = self._render_manifest(manifest)
        for relative, content in contents.items():
            try:
                destination = safe_join(target, relative)
            except PathViolation as exc:
                raise SkillExistsError(f"Unsafe export path: {relative!r}") from exc
            atomic_write_text(destination, content)
        logger.debug(
            "exported skill",
            extra={"exporter": self.id, "skill": bundle.metadata.name, "target": str(target)},
        )
        return target

    def export_generated(
        self, skill: GeneratedSkill, repo_root: Path, *, force: bool = False
    ) -> Path:
        manifest = skill.build_manifest(
            tool_version=skill.bundle.metadata.metadata.get("generator-version", ""),
            repository=skill.bundle.metadata.metadata.get("source-repository", ""),
            git_commit=None,
            git_dirty=None,
        )
        return self.export(skill.bundle, repo_root, manifest=manifest, force=force)

    def export_directory(
        self, source_dir: Path, repo_root: Path, *, force: bool = False
    ) -> list[str]:
        """Export every StoredSkill-shaped directory under ``source_dir``."""
        from skillforge.skills.store import list_skill_directories

        exported: list[str] = []
        for directory in list_skill_directories(source_dir):
            stored: StoredSkill = read_bundle(directory)
            self.export(stored.bundle, repo_root, manifest=stored.manifest, force=force)
            exported.append(stored.name)
        return exported

    # ------------------------------------------------------------------- notes
    def discover(self, repo_root: Path) -> list[str]:
        """Names of skills already exported for this agent."""
        root = self.skills_root(repo_root)
        if not root.is_dir():
            return []
        return sorted(
            child.name
            for child in root.iterdir()
            if child.is_dir() and (child / "SKILL.md").is_file()
        )

    def compatibility_notes(self) -> list[str]:
        """Facts about the target format, verified against its published docs."""
        return []

    # --------------------------------------------------------------- internals
    def _assert_supported_metadata(self, metadata: SkillMetadata) -> None:
        """Never export a skill whose frontmatter would be invalid for the target."""
        return None

    def _render_manifest(self, manifest: SkillManifest) -> str:
        return manifest.model_dump_json(indent=2)
