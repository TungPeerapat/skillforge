"""Evaluation scenarios.

A scenario pairs a fixture repository with a task statement and deterministic
checks. Scenarios live in ``benchmarks/scenarios/*.toml`` so they can be edited
without touching code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from skillforge.errors import ConfigError, NotFoundError
from skillforge.utils.toml import load_toml_file


class ScenarioCheck(BaseModel):
    """One deterministic assertion about the generated skills."""

    model_config = ConfigDict(extra="forbid")

    type: Literal[
        "skill_generated",
        "workflow_present",
        "command_present",
        "command_with_evidence",
        "validation_passes",
        "no_dangerous_commands",
        "skill_md_token_budget",
        "technology_detected",
        "risk_detected",
        "unknown_recorded",
    ]
    skill: str = "*"
    contains: str = ""
    value: str = ""
    max_tokens: int = 5000
    description: str = ""


class Scenario(BaseModel):
    """A reproducible evaluation case."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    fixture: str
    task: str = ""
    description: str = ""
    expected_skills: list[str] = Field(default_factory=list)
    checks: list[ScenarioCheck] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def load_scenario(path: Path) -> Scenario:
    """Load and validate one scenario file."""
    data = load_toml_file(path)
    try:
        scenario = Scenario.model_validate(data)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        raise ConfigError(f"Invalid scenario {path.name}: {details}") from exc
    return scenario


def load_scenarios(directory: Path) -> list[Scenario]:
    """Load every ``*.toml`` scenario in ``directory`` (sorted, deterministic)."""
    if not directory.is_dir():
        raise NotFoundError(f"Scenario directory not found: {directory}")
    scenarios = [load_scenario(path) for path in sorted(directory.glob("*.toml"))]
    if not scenarios:
        raise NotFoundError(f"No scenarios found in {directory}")
    ids = [scenario.id for scenario in scenarios]
    duplicates = {item for item in ids if ids.count(item) > 1}
    if duplicates:
        raise ConfigError(f"Duplicate scenario ids: {', '.join(sorted(duplicates))}")
    return scenarios
