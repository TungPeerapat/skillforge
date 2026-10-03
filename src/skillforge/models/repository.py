"""Repository-level analysis models."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from skillforge.models.common import Certainty, Evidence
from skillforge.models.workflow import API, Command, Database, Risk, Service, Workflow

SCHEMA_VERSION = 1


class TechnologyKind(StrEnum):
    LANGUAGE = "language"
    RUNTIME = "runtime"
    FRAMEWORK = "framework"
    LIBRARY = "library"
    PACKAGE_MANAGER = "package_manager"
    BUILD_TOOL = "build_tool"
    TEST_FRAMEWORK = "test_framework"
    DATABASE = "database"
    DATABASE_TOOL = "database_tool"
    CONTAINER = "container"
    ORCHESTRATION = "orchestration"
    CI = "ci"
    CLOUD = "cloud"
    CACHE = "cache"
    QUEUE = "queue"
    WEB_SERVER = "web_server"
    CONFIG = "config"
    TOOL = "tool"


class DependencyScope(StrEnum):
    RUNTIME = "runtime"
    DEVELOPMENT = "development"
    OPTIONAL = "optional"
    PEER = "peer"
    UNKNOWN = "unknown"


class ComponentKind(StrEnum):
    APPLICATION = "application"
    SERVICE = "service"
    LIBRARY = "library"
    PACKAGE = "package"
    CLI = "cli"
    UNKNOWN = "unknown"


class DocKind(StrEnum):
    README = "readme"
    CONTRIBUTING = "contributing"
    ARCHITECTURE = "architecture"
    CHANGELOG = "changelog"
    GUIDE = "guide"
    API = "api"
    OTHER = "other"


class DetectedTechnology(BaseModel):
    """A technology inferred from repository evidence."""

    model_config = ConfigDict(extra="forbid")

    kind: TechnologyKind
    name: str
    version: str | None = None
    certainty: Certainty = Certainty.FACT
    confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    evidence: list[Evidence] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @property
    def display(self) -> str:
        return f"{self.name} {self.version}" if self.version else self.name


class Dependency(BaseModel):
    """A declared dependency from a manifest or lockfile."""

    model_config = ConfigDict(extra="forbid")

    ecosystem: str
    name: str
    version_spec: str | None = None
    resolved_version: str | None = None
    scope: DependencyScope = DependencyScope.UNKNOWN
    manifest: str = ""
    certainty: Certainty = Certainty.FACT
    evidence: list[Evidence] = Field(default_factory=list)


class LanguageStat(BaseModel):
    language: str
    files: int = 0
    lines: int = 0


class FileStats(BaseModel):
    """Aggregate file statistics (no file list — profiles stay small)."""

    total_files: int = 0
    analyzed_files: int = 0
    skipped_files: int = 0
    ignored_entries: int = 0
    total_bytes: int = 0
    analyzed_bytes: int = 0
    total_lines: int = 0
    truncated_files: int = 0
    languages: list[LanguageStat] = Field(default_factory=list)
    categories: dict[str, int] = Field(default_factory=dict)
    skipped_reasons: dict[str, int] = Field(default_factory=dict)


class ProjectComponent(BaseModel):
    """A buildable unit inside the repository (app, package, service)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    path: str = "."
    kind: ComponentKind = ComponentKind.UNKNOWN
    ecosystems: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    manifests: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    certainty: Certainty = Certainty.FACT


class DocFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    kind: DocKind = DocKind.OTHER
    title: str = ""
    bytes: int = 0


class GitSummary(BaseModel):
    available: bool = False
    in_work_tree: bool = False
    commit: str | None = None
    branch: str | None = None
    dirty: bool | None = None
    host: str | None = None

    @property
    def summary(self) -> str:
        if not self.in_work_tree:
            return "not a git work tree"
        parts: list[str] = []
        if self.branch:
            parts.append(f"branch {self.branch}")
        if self.commit:
            parts.append(f"commit {self.commit}")
        if self.dirty is not None:
            parts.append("dirty" if self.dirty else "clean")
        return ", ".join(parts) if parts else "git work tree"


class RepositoryProfile(BaseModel):
    """The complete, deterministic result of repository analysis."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    tool_version: str = ""
    generated_at: datetime
    name: str
    root_path: str
    git: GitSummary = Field(default_factory=GitSummary)
    stats: FileStats = Field(default_factory=FileStats)
    components: list[ProjectComponent] = Field(default_factory=list)
    technologies: list[DetectedTechnology] = Field(default_factory=list)
    dependencies: list[Dependency] = Field(default_factory=list)
    commands: list[Command] = Field(default_factory=list)
    workflows: list[Workflow] = Field(default_factory=list)
    services: list[Service] = Field(default_factory=list)
    databases: list[Database] = Field(default_factory=list)
    apis: list[API] = Field(default_factory=list)
    docs: list[DocFile] = Field(default_factory=list)
    risks: list[Risk] = Field(default_factory=list)
    unknowns: list[Unknown] = Field(default_factory=list)
    environment_keys: list[str] = Field(default_factory=list)
    environment_files: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    # ------------------------------------------------------------------ helpers
    def technology_names(self, kind: TechnologyKind | None = None) -> list[str]:
        return [item.name for item in self.technologies if kind is None or item.kind is kind]

    def has_technology(self, name: str) -> bool:
        lowered = name.lower()
        return any(item.name.lower() == lowered for item in self.technologies)

    def find_dependency(self, name: str) -> Dependency | None:
        lowered = name.lower()
        for dependency in self.dependencies:
            if dependency.name.lower() == lowered:
                return dependency
        return None

    def primary_languages(self, *, limit: int = 5) -> list[str]:
        ordered = sorted(self.stats.languages, key=lambda item: item.lines, reverse=True)
        return [item.language for item in ordered[:limit]]

    def commands_for_purpose(self, purpose: str) -> list[Command]:
        return [command for command in self.commands if command.purpose.value == purpose]

    def workflows_by_category(self, category: str) -> list[Workflow]:
        return [workflow for workflow in self.workflows if workflow.category.value == category]

    def summary_lines(self) -> list[str]:
        lines: list[str] = []
        if self.stats.total_files:
            lines.append(
                f"{self.stats.total_files} files, {self.stats.total_lines:,} lines, "
                f"{self.stats.total_bytes // 1024:,} KiB"
            )
        languages = self.primary_languages(limit=4)
        if languages:
            lines.append("Languages: " + ", ".join(languages))
        return lines

    def top_risks(self, *, minimum: str = "medium", limit: int = 10) -> list[Risk]:
        order = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
        threshold = order.get(minimum, 2)
        risky = [risk for risk in self.risks if order.get(risk.severity.value, 0) >= threshold]
        risky.sort(key=lambda risk: order.get(risk.severity.value, 0), reverse=True)
        return risky[:limit]


# ``Unknown`` lives in models.common; re-exported here for convenience so that
# ``from skillforge.models.repository import Unknown`` keeps working.
from skillforge.models.common import Unknown  # noqa: E402

__all__ = [
    "API",
    "SCHEMA_VERSION",
    "Command",
    "ComponentKind",
    "Database",
    "Dependency",
    "DependencyScope",
    "DetectedTechnology",
    "DocFile",
    "DocKind",
    "FileStats",
    "GitSummary",
    "LanguageStat",
    "ProjectComponent",
    "RepositoryProfile",
    "Risk",
    "Service",
    "TechnologyKind",
    "Unknown",
    "Workflow",
]
