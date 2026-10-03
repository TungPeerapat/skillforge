"""Portable skill representation and planning models.

These models are agent-neutral: exporters transform a :class:`SkillBundle`
into whatever layout a specific agent expects. Nothing here knows about
Claude Code, Codex, or OpenCode.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from skillforge.models.common import Certainty, Evidence
from skillforge.utils.markdown import render_frontmatter
from skillforge.utils.tokens import estimate_tokens

SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
SKILL_NAME_MAX = 64
DESCRIPTION_MAX = 1024
COMPATIBILITY_MAX = 500
SKILL_MD_MAX_LINES = 500
SKILL_MD_TOKEN_WARN = 5000
MANIFEST_FILENAME = ".skillforge.json"
SUPPORTED_RESOURCE_DIRS = ("references", "scripts", "assets")


def is_valid_skill_name(name: str) -> bool:
    """Agent Skills ``name`` grammar (also enforced by OpenCode and Claude Code)."""
    return bool(name) and len(name) <= SKILL_NAME_MAX and bool(SKILL_NAME_PATTERN.match(name))


class SkillMetadata(BaseModel):
    """The YAML frontmatter of ``SKILL.md`` (Agent Skills specification)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    license: str | None = None
    compatibility: str | None = None
    allowed_tools: str | None = Field(default=None, alias="allowed-tools")
    metadata: dict[str, str] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if not is_valid_skill_name(value):
            raise ValueError(
                "skill name must match ^[a-z0-9]+(-[a-z0-9]+)*$, be 1-64 characters, "
                "and must not contain consecutive hyphens"
            )
        return value

    @field_validator("description")
    @classmethod
    def _validate_description(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("description must not be empty")
        if len(stripped) > DESCRIPTION_MAX:
            raise ValueError(f"description must be at most {DESCRIPTION_MAX} characters")
        return stripped

    @field_validator("compatibility")
    @classmethod
    def _validate_compatibility(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        if len(stripped) > COMPATIBILITY_MAX:
            raise ValueError(f"compatibility must be at most {COMPATIBILITY_MAX} characters")
        return stripped

    def to_frontmatter(self) -> dict[str, object]:
        """Render to an ordered mapping for YAML emission."""
        data: dict[str, object] = {"name": self.name, "description": self.description}
        if self.license:
            data["license"] = self.license
        if self.compatibility:
            data["compatibility"] = self.compatibility
        if self.allowed_tools:
            data["allowed-tools"] = self.allowed_tools
        if self.metadata:
            data["metadata"] = dict(self.metadata)
        return data


class SkillFile(BaseModel):
    """A supporting file inside a skill directory (references/scripts/assets)."""

    model_config = ConfigDict(extra="forbid")

    path: str
    content: str
    executable: bool = False
    description: str = ""

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        from skillforge.security.paths import assert_safe_relative

        cleaned = assert_safe_relative(value)
        if cleaned == ".":
            raise ValueError("skill file path must not be empty")
        return cleaned

    @property
    def size(self) -> int:
        return len(self.content.encode("utf-8"))

    @property
    def token_estimate(self) -> int:
        return estimate_tokens(self.content)


class ProvenanceOrigin(StrEnum):
    DETERMINISTIC = "deterministic"
    TEMPLATE = "template"
    LLM = "llm"
    HUMAN = "human"


class ProvenanceEntry(BaseModel):
    """Why a piece of a generated skill exists, and where the claim came from."""

    model_config = ConfigDict(extra="forbid")

    target: str
    claim: str
    origin: ProvenanceOrigin = ProvenanceOrigin.DETERMINISTIC
    evidence: list[Evidence] = Field(default_factory=list)

    @property
    def sources(self) -> list[str]:
        return [item.label for item in self.evidence]


class GenerationMode(StrEnum):
    DETERMINISTIC = "deterministic"
    LLM_ASSISTED = "llm-assisted"
    HYBRID = "hybrid"


class SkillBundle(BaseModel):
    """An in-memory skill directory (SKILL.md plus supporting files)."""

    model_config = ConfigDict(extra="forbid")

    metadata: SkillMetadata
    body: str = ""
    files: dict[str, SkillFile] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_and_safe_paths(self) -> SkillBundle:
        seen: set[str] = set()
        for path in self.files:
            lowered = path.lower()
            if lowered in seen:
                raise ValueError(f"duplicate skill file path (case-insensitive): {path}")
            seen.add(lowered)
        return self

    # ------------------------------------------------------------------ access
    def file(self, path: str) -> SkillFile | None:
        return self.files.get(path)

    def paths(self) -> list[str]:
        return sorted(self.files)

    def add_file(self, file: SkillFile, *, overwrite: bool = False) -> None:
        if file.path in self.files and not overwrite:
            raise ValueError(f"skill file already exists: {file.path}")
        self.files[file.path] = file

    def skill_md(self) -> str:
        """Full ``SKILL.md`` text: frontmatter plus body."""
        frontmatter = render_frontmatter(self.metadata.to_frontmatter())
        body = self.body if self.body.endswith("\n") else self.body + "\n"
        return f"{frontmatter}\n{body}"

    def all_contents(self) -> dict[str, str]:
        """Every file in the bundle, including ``SKILL.md``."""
        contents = {"SKILL.md": self.skill_md()}
        for path, file in self.files.items():
            contents[path] = file.content
        return contents

    def total_tokens(self) -> int:
        return sum(estimate_tokens(content) for content in self.all_contents().values())

    @property
    def line_count(self) -> int:
        return len(self.body.splitlines())


class SkillCandidate(BaseModel):
    """A recommended skill, produced by the planner."""

    model_config = ConfigDict(extra="forbid")

    name: str
    title: str
    reason: str
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    certainty: Certainty = Certainty.INFERENCE
    dependencies: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    requires: list[str] = Field(default_factory=list)
    priority: int = Field(default=50, ge=0, le=1000)
    tags: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    recommended: bool = True

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if not is_valid_skill_name(value):
            raise ValueError(f"invalid skill name: {value!r}")
        return value


class SkillPlan(BaseModel):
    """The planner's output for one repository."""

    model_config = ConfigDict(extra="forbid")

    repository: str
    generated_at: datetime
    candidates: list[SkillCandidate] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    def candidate(self, name: str) -> SkillCandidate | None:
        for item in self.candidates:
            if item.name == name:
                return item
        return None

    @property
    def names(self) -> list[str]:
        return [item.name for item in self.candidates]


class SkillManifest(BaseModel):
    """Contents of the ``.skillforge.json`` file inside a generated skill."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    tool_version: str = ""
    skill: str
    title: str = ""
    generated_at: datetime
    mode: GenerationMode = GenerationMode.DETERMINISTIC
    provider: str | None = None
    source_repository: str = ""
    source_git_commit: str | None = None
    source_git_dirty: bool | None = None
    reason: str = ""
    confidence: float = 0.0
    evidence_count: int = 0
    file_hashes: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    @property
    def filename(self) -> str:
        return MANIFEST_FILENAME


class GeneratedSkill(BaseModel):
    """A generated skill plus provenance and generation metadata."""

    model_config = ConfigDict(extra="forbid")

    bundle: SkillBundle
    candidate: SkillCandidate | None = None
    provenance: list[ProvenanceEntry] = Field(default_factory=list)
    mode: GenerationMode = GenerationMode.DETERMINISTIC
    provider: str | None = None
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime

    @property
    def name(self) -> str:
        return self.bundle.metadata.name

    def provenance_for(self, target: str) -> list[ProvenanceEntry]:
        return [entry for entry in self.provenance if entry.target == target]

    def build_manifest(
        self, *, tool_version: str, repository: str, git_commit: str | None, git_dirty: bool | None
    ) -> SkillManifest:
        from skillforge.utils.fs import sha256_text

        contents = self.bundle.all_contents()
        return SkillManifest(
            tool_version=tool_version,
            skill=self.name,
            title=self.candidate.title if self.candidate else self.name,
            generated_at=self.created_at,
            mode=self.mode,
            provider=self.provider,
            source_repository=repository,
            source_git_commit=git_commit,
            source_git_dirty=git_dirty,
            reason=self.candidate.reason if self.candidate else "",
            confidence=self.candidate.confidence if self.candidate else 0.0,
            evidence_count=sum(len(entry.evidence) for entry in self.provenance),
            file_hashes={path: sha256_text(content) for path, content in contents.items()},
            warnings=self.warnings,
        )
