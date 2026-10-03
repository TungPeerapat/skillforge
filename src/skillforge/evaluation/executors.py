"""Agent executors for evaluation.

SkillForge measures its own deterministic pipeline end to end. It does **not**
run coding agents itself in this release: doing so requires sandboxing, budgets,
and consent that are out of scope for v0.1. Instead:

* :class:`NullAgentExecutor` reports every agent metric as ``NOT MEASURED``.
* :class:`RecordedAgentExecutor` reads previously recorded runs from
  ``benchmarks/recorded/<scenario>[-without].json`` and measures them honestly.

Recording format (one file per scenario and condition)::

    {
      "scenario": "fastapi-health-endpoint",
      "condition": "with-skill",          // or "without-skill"
      "agent": "claude-code",
      "model": "claude-sonnet-4-5",
      "success": true,
      "tokens": 18432,
      "tool_calls": 11,
      "latency_seconds": 42.5,
      "notes": "transcript path or reviewer comment"
    }
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from skillforge.errors import SkillForgeError
from skillforge.logging import get_logger

logger = get_logger("evaluation.executors")

CONDITION_WITH = "with-skill"
CONDITION_WITHOUT = "without-skill"


@dataclass(frozen=True)
class AgentRunResult:
    """Outcome of running one task with an agent."""

    condition: str
    measured: bool = False
    success: bool | None = None
    tokens: int | None = None
    tool_calls: int | None = None
    latency_seconds: float | None = None
    agent: str = ""
    model: str = ""
    notes: str = ""


class AgentExecutor(Protocol):
    """Extension point for real agent execution."""

    id: str

    def run(self, scenario_id: str, task: str, condition: str) -> AgentRunResult:
        """Run ``task`` in the fixture under ``condition`` and report metrics."""


class NullAgentExecutor:
    """Reports NOT MEASURED for every agent metric."""

    id = "null"

    def run(self, scenario_id: str, task: str, condition: str) -> AgentRunResult:
        return AgentRunResult(
            condition=condition,
            measured=False,
            notes="no agent executor configured; agent metrics are NOT MEASURED",
        )


class RecordedAgentExecutor:
    """Reads recorded agent runs so real numbers can be reported without re-running."""

    id = "recorded"

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def run(self, scenario_id: str, task: str, condition: str) -> AgentRunResult:
        suffix = "" if condition == CONDITION_WITH else "-without"
        path = self._directory / f"{scenario_id}{suffix}.json"
        if not path.is_file():
            return AgentRunResult(
                condition=condition,
                measured=False,
                notes=f"no recording at {path.name}",
            )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SkillForgeError(f"Cannot read recording {path}: {exc}") from exc
        if str(payload.get("scenario", scenario_id)) != scenario_id:
            logger.warning("recording scenario mismatch", extra={"path": str(path)})
        return AgentRunResult(
            condition=condition,
            measured=payload.get("success") is not None,
            success=payload.get("success"),
            tokens=payload.get("tokens"),
            tool_calls=payload.get("tool_calls"),
            latency_seconds=payload.get("latency_seconds"),
            agent=str(payload.get("agent") or ""),
            model=str(payload.get("model") or ""),
            notes=str(payload.get("notes") or ""),
        )
