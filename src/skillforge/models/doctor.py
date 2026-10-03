"""Environment inspection models used by ``skillforge doctor``."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class CheckStatus(StrEnum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    MISSING = "missing"
    SKIPPED = "skipped"

    @property
    def symbol(self) -> str:
        return {
            CheckStatus.OK: "✓",
            CheckStatus.WARN: "!",
            CheckStatus.FAIL: "✗",
            CheckStatus.MISSING: "-",
            CheckStatus.SKIPPED: "·",
        }[self]

    @property
    def is_problem(self) -> bool:
        return self in (CheckStatus.WARN, CheckStatus.FAIL)


class ToolCheck(BaseModel):
    """Result of checking one tool, agent, or setting."""

    model_config = ConfigDict(extra="forbid")

    name: str
    category: str = "tools"
    status: CheckStatus = CheckStatus.SKIPPED
    version: str = ""
    path: str = ""
    detail: str = ""
    required: bool = False

    @property
    def display_value(self) -> str:
        if self.status is CheckStatus.OK and self.version:
            return self.version
        if self.detail:
            return self.detail
        return {
            CheckStatus.OK: "available",
            CheckStatus.WARN: "warning",
            CheckStatus.FAIL: "failed",
            CheckStatus.MISSING: "not found",
            CheckStatus.SKIPPED: "skipped",
        }[self.status]


class DoctorReport(BaseModel):
    """Full environment report."""

    model_config = ConfigDict(extra="forbid")

    tool_version: str = ""
    generated_at: datetime
    python_version: str = ""
    platform: str = ""
    checks: list[ToolCheck] = Field(default_factory=list)
    config_source: str | None = None
    config_issues: list[str] = Field(default_factory=list)
    skill_output: str = ""
    generated_skills: int = 0
    notes: list[str] = Field(default_factory=list)

    def by_category(self, category: str) -> list[ToolCheck]:
        return [check for check in self.checks if check.category == category]

    @property
    def categories(self) -> list[str]:
        seen: list[str] = []
        for check in self.checks:
            if check.category not in seen:
                seen.append(check.category)
        return seen

    @property
    def problems(self) -> list[ToolCheck]:
        return [check for check in self.checks if check.status.is_problem]

    @property
    def ok(self) -> bool:
        return not any(check.status is CheckStatus.FAIL for check in self.checks)
