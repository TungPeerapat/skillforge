"""Validation result models."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class FindingSeverity(StrEnum):
    """Severity of a validation finding (distinct from repository risk)."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"

    @property
    def label(self) -> str:
        return self.value.upper()


class ValidationFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    severity: FindingSeverity
    message: str
    location: str = ""
    hint: str = ""

    @property
    def is_error(self) -> bool:
        return self.severity is FindingSeverity.ERROR


class ValidationResult(BaseModel):
    """Aggregated findings for one target (a skill, a bundle, a directory)."""

    model_config = ConfigDict(extra="forbid")

    target: str
    findings: list[ValidationFinding] = Field(default_factory=list)
    checked: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    def add(
        self,
        code: str,
        severity: FindingSeverity,
        message: str,
        *,
        location: str = "",
        hint: str = "",
    ) -> None:
        self.findings.append(
            ValidationFinding(
                code=code, severity=severity, message=message, location=location, hint=hint
            )
        )

    def extend(self, findings: list[ValidationFinding]) -> None:
        self.findings.extend(findings)

    def merge(self, other: ValidationResult) -> None:
        self.findings.extend(other.findings)
        for item in other.checked:
            if item not in self.checked:
                self.checked.append(item)

    @property
    def errors(self) -> list[ValidationFinding]:
        return [item for item in self.findings if item.severity is FindingSeverity.ERROR]

    @property
    def warnings(self) -> list[ValidationFinding]:
        return [item for item in self.findings if item.severity is FindingSeverity.WARNING]

    @property
    def infos(self) -> list[ValidationFinding]:
        return [item for item in self.findings if item.severity is FindingSeverity.INFO]

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def clean(self) -> bool:
        return not self.findings

    def sorted_findings(self) -> list[ValidationFinding]:
        order = {FindingSeverity.ERROR: 0, FindingSeverity.WARNING: 1, FindingSeverity.INFO: 2}
        return sorted(
            self.findings, key=lambda item: (order[item.severity], item.code, item.location)
        )

    def summary(self) -> str:
        parts = [f"{len(self.errors)} error(s)", f"{len(self.warnings)} warning(s)"]
        if self.infos:
            parts.append(f"{len(self.infos)} info")
        return ", ".join(parts)
