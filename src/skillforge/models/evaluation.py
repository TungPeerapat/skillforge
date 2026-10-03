"""Evaluation models.

Every metric records whether it was actually measured. SkillForge must never
print a fabricated benchmark number: when a measurement is unavailable the
report renders ``NOT MEASURED``.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

NOT_MEASURED = "NOT MEASURED"


class MetricValue(BaseModel):
    """One measured (or explicitly unmeasured) metric."""

    model_config = ConfigDict(extra="forbid")

    name: str
    value: float | None = None
    unit: str = ""
    measured: bool = False
    method: str = ""
    note: str = ""

    @classmethod
    def not_measured(cls, name: str, *, method: str = "", note: str = "") -> MetricValue:
        return cls(name=name, value=None, measured=False, method=method, note=note)

    @classmethod
    def measured_value(
        cls, name: str, value: float, *, unit: str = "", method: str = "", note: str = ""
    ) -> MetricValue:
        return cls(name=name, value=value, unit=unit, measured=True, method=method, note=note)

    def display(self) -> str:
        if not self.measured or self.value is None:
            return NOT_MEASURED
        if self.unit:
            return f"{self.value:g} {self.unit}"
        return f"{self.value:g}"


class CheckOutcome(BaseModel):
    """Result of one deterministic evaluation check."""

    model_config = ConfigDict(extra="forbid")

    id: str
    description: str
    passed: bool | None = None
    detail: str = ""
    measured: bool = True

    @property
    def label(self) -> str:
        if not self.measured or self.passed is None:
            return NOT_MEASURED
        return "PASS" if self.passed else "FAIL"


class ScenarioResult(BaseModel):
    """Evaluation of a single scenario against a fixture repository."""

    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    title: str
    fixture: str
    description: str = ""
    skills: list[str] = Field(default_factory=list)
    outcomes: list[CheckOutcome] = Field(default_factory=list)
    metrics: list[MetricValue] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(outcome.passed for outcome in self.outcomes if outcome.measured)

    def measured_metrics(self) -> list[MetricValue]:
        return [metric for metric in self.metrics if metric.measured]


class ComparisonRow(BaseModel):
    """With-skill versus without-skill comparison for one metric."""

    model_config = ConfigDict(extra="forbid")

    metric: str
    without_skill: MetricValue
    with_skill: MetricValue
    note: str = ""


class EvaluationReport(BaseModel):
    """Top-level evaluation report."""

    model_config = ConfigDict(extra="forbid")

    tool_version: str = ""
    generated_at: datetime
    scenarios: list[ScenarioResult] = Field(default_factory=list)
    comparisons: list[ComparisonRow] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @property
    def checks_passed(self) -> int:
        return sum(1 for scenario in self.scenarios for o in scenario.outcomes if o.passed)

    @property
    def checks_failed(self) -> int:
        return sum(
            1
            for scenario in self.scenarios
            for o in scenario.outcomes
            if o.measured and o.passed is False
        )

    @property
    def checks_total(self) -> int:
        return sum(1 for scenario in self.scenarios for o in scenario.outcomes if o.measured)
