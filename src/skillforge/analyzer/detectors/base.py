"""Detector framework: shared context, helpers, and result aggregation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml

from skillforge.analyzer.scanner import FileRecord, ScanResult
from skillforge.config import AnalysisSettings
from skillforge.logging import get_logger
from skillforge.models import (
    API,
    Certainty,
    Command,
    Database,
    Dependency,
    DetectedTechnology,
    DocFile,
    Evidence,
    ProjectComponent,
    Risk,
    Service,
    Unknown,
)
from skillforge.models.workflow import CommandPurpose, CommandSource

logger = get_logger("analyzer.detectors")


def fact(
    source: str,
    locator: str = "",
    detail: str = "",
    *,
    snippet: str = "",
    weight: float = 0.8,
) -> Evidence:
    """Evidence for something directly observed in a file."""
    return Evidence(
        kind=Certainty.FACT,
        source=source,
        locator=locator,
        detail=detail,
        snippet=snippet,
        weight=weight,
    )


def inference(
    source: str,
    locator: str = "",
    detail: str = "",
    *,
    snippet: str = "",
    weight: float = 0.5,
) -> Evidence:
    """Evidence supporting a derived (not directly observed) statement."""
    return Evidence(
        kind=Certainty.INFERENCE,
        source=source,
        locator=locator,
        detail=detail,
        snippet=snippet,
        weight=weight,
    )


@dataclass
class Detection:
    """Everything a detector contributes to the repository profile."""

    technologies: list[DetectedTechnology] = field(default_factory=list)
    dependencies: list[Dependency] = field(default_factory=list)
    components: list[ProjectComponent] = field(default_factory=list)
    commands: list[Command] = field(default_factory=list)
    services: list[Service] = field(default_factory=list)
    databases: list[Database] = field(default_factory=list)
    apis: list[API] = field(default_factory=list)
    docs: list[DocFile] = field(default_factory=list)
    risks: list[Risk] = field(default_factory=list)
    unknowns: list[Unknown] = field(default_factory=list)
    env_keys: list[str] = field(default_factory=list)
    env_files: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def merge(self, other: Detection) -> None:
        """Append another detection's findings to this one."""
        for attribute in (
            "technologies",
            "dependencies",
            "components",
            "commands",
            "services",
            "databases",
            "apis",
            "docs",
            "risks",
            "unknowns",
            "env_keys",
            "env_files",
            "notes",
        ):
            getattr(self, attribute).extend(getattr(other, attribute))


class DetectionContext:
    """Read-only access to scanned files with parsing helpers and caching."""

    def __init__(self, root: Path, scan: ScanResult, settings: AnalysisSettings) -> None:
        self.root = root
        self.scan = scan
        self.settings = settings
        self._cache: dict[str, Any] = {}

    # ------------------------------------------------------------- file access
    def read(self, path: str) -> str | None:
        return self.scan.read(path)

    def exists(self, path: str) -> bool:
        return self.scan.record(path) is not None

    def records_named(self, *names: str) -> list[FileRecord]:
        return self.scan.records_named(*names)

    def records_with_suffix(self, *suffixes: str) -> list[FileRecord]:
        return self.scan.records_with_suffix(*suffixes)

    def source_files(self) -> list[FileRecord]:
        return self.scan.source_files()

    # ---------------------------------------------------------------- parsers
    def json(self, path: str) -> Any | None:
        key = f"json:{path}"
        if key in self._cache:
            return self._cache[key]
        text = self.read(path)
        value: Any = None
        if text is not None:
            try:
                value = json.loads(text)
            except ValueError as exc:
                logger.debug("invalid JSON", extra={"path": path, "error": str(exc)})
                value = None
        self._cache[key] = value
        return value

    def toml(self, path: str) -> dict[str, Any] | None:
        key = f"toml:{path}"
        if key in self._cache:
            return self._cache[key]
        text = self.read(path)
        value: dict[str, Any] | None = None
        if text is not None:
            import tomllib

            try:
                value = tomllib.loads(text)
            except tomllib.TOMLDecodeError as exc:
                logger.debug("invalid TOML", extra={"path": path, "error": str(exc)})
                value = None
        self._cache[key] = value
        return value

    def yaml(self, path: str) -> Any | None:
        key = f"yaml:{path}"
        if key in self._cache:
            return self._cache[key]
        text = self.read(path)
        value: Any = None
        if text is not None:
            try:
                value = yaml.safe_load(text)
            except yaml.YAMLError as exc:
                logger.debug("invalid YAML", extra={"path": path, "error": str(exc)})
                value = None
        self._cache[key] = value
        return value

    def make_command(
        self,
        command: str,
        *,
        source: CommandSource,
        path: str,
        locator: str = "",
        purpose: CommandPurpose = CommandPurpose.OTHER,
        cwd: str = ".",
        certainty: Certainty = Certainty.FACT,
        confidence: float = 0.8,
        detail: str = "",
        script_body: str | None = None,
        placeholders: list[str] | None = None,
        notes: list[str] | None = None,
    ) -> Command:
        """Build a :class:`Command` via the shared discovery builder."""
        from skillforge.discovery.commands import build_command

        return build_command(
            command,
            source=source,
            path=path,
            locator=locator,
            purpose=purpose,
            cwd=cwd,
            certainty=certainty,
            confidence=confidence,
            detail=detail,
            script_body=script_body,
            placeholders=placeholders,
            notes=notes,
        )


class EcosystemDetector(Protocol):
    """A deterministic detector for one ecosystem or concern."""

    id: str

    def detect(self, context: DetectionContext) -> Detection:
        """Return everything this detector observed. Must not raise on bad input."""

    def applies(self, context: DetectionContext) -> bool:
        """Cheap check to skip detectors that cannot match this repository."""
