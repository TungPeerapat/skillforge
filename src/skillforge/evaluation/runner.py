"""Evaluation runner.

Runs each scenario through the real pipeline (scan → plan → generate → validate),
executes the deterministic checks, and reports measured metrics. Agent metrics
come from an :class:`~skillforge.evaluation.executors.AgentExecutor`; without one
they are reported as ``NOT MEASURED``.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path

from skillforge import __version__
from skillforge.analyzer import analyze_repository
from skillforge.config import Settings, load_settings
from skillforge.evaluation.checks import EvalContext, run_check
from skillforge.evaluation.executors import (
    CONDITION_WITH,
    CONDITION_WITHOUT,
    AgentExecutor,
    NullAgentExecutor,
)
from skillforge.evaluation.scenarios import Scenario
from skillforge.generator import SkillGenerator
from skillforge.logging import get_logger, trace_span
from skillforge.models import (
    CheckOutcome,
    ComparisonRow,
    EvaluationReport,
    GeneratedSkill,
    MetricValue,
    ScenarioResult,
)
from skillforge.planner import SkillPlanner
from skillforge.security.command_risk import classify_command
from skillforge.utils.markdown import fenced_code_blocks
from skillforge.utils.tokens import estimate_tokens
from skillforge.validator import SkillValidator

logger = get_logger("evaluation")


class EvaluationRunner:
    """Run scenarios and build an honest report."""

    def __init__(
        self,
        *,
        repo_root: Path,
        scenarios: list[Scenario],
        executor: AgentExecutor | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._repo_root = repo_root
        self._scenarios = scenarios
        self._executor: AgentExecutor = executor or NullAgentExecutor()
        self._settings = settings

    def run(self) -> EvaluationReport:
        results = [self._run_scenario(scenario) for scenario in self._scenarios]
        comparisons = self._comparisons(results)
        notes = [
            "Deterministic metrics are measured by running the real pipeline on the fixture.",
            "Agent metrics (task success, tokens, tool calls, latency) are measured only when an "
            "agent executor or a recording is provided; otherwise they show NOT MEASURED.",
            "SkillForge never fabricates benchmark numbers.",
        ]
        return EvaluationReport(
            tool_version=__version__,
            generated_at=datetime.now(UTC),
            scenarios=results,
            comparisons=comparisons,
            notes=notes,
        )

    # --------------------------------------------------------------- scenario
    def _run_scenario(self, scenario: Scenario) -> ScenarioResult:
        fixture = (self._repo_root / scenario.fixture).resolve()
        if not fixture.is_dir():
            return ScenarioResult(
                scenario_id=scenario.id,
                title=scenario.title,
                fixture=scenario.fixture,
                description=scenario.description,
                notes=[f"fixture not found: {scenario.fixture}"],
                outcomes=[
                    CheckOutcome(
                        id="fixture",
                        description=f"fixture {scenario.fixture} exists",
                        passed=False,
                        detail=str(fixture),
                    )
                ],
            )
        settings = self._settings or load_settings(fixture, env={})

        with trace_span("evaluate", scenario=scenario.id):
            started = time.perf_counter()
            analysis = analyze_repository(fixture, settings)
            analysis_ms = (time.perf_counter() - started) * 1000

            plan = SkillPlanner().plan(analysis.profile)
            generator = SkillGenerator(settings)
            started = time.perf_counter()
            skills = generator.generate(analysis.profile, plan=plan)
            generation_ms = (time.perf_counter() - started) * 1000
            validator = SkillValidator(profile=analysis.profile)
            validations = {skill.name: validator.validate_generated(skill) for skill in skills}

        context = EvalContext(profile=analysis.profile, skills=skills, validations=validations)
        outcomes: list[CheckOutcome] = []
        for check in scenario.checks:
            result = run_check(check, context)
            outcomes.append(
                CheckOutcome(
                    id=result.id,
                    description=result.description,
                    passed=result.passed,
                    detail=result.detail,
                    measured=result.measured,
                )
            )
        for expected in scenario.expected_skills:
            found = context.skill(expected) is not None
            outcomes.append(
                CheckOutcome(
                    id=f"expected-skill:{expected}",
                    description=f"expected skill '{expected}' was recommended",
                    passed=found,
                )
            )

        metrics = self._metrics(scenario, context, analysis_ms, generation_ms)
        notes = list(scenario.notes)
        for skill in skills:
            notes.extend(f"{skill.name}: {warning}" for warning in skill.warnings)
        return ScenarioResult(
            scenario_id=scenario.id,
            title=scenario.title,
            fixture=scenario.fixture,
            description=scenario.description or scenario.task,
            skills=[skill.name for skill in skills],
            outcomes=outcomes,
            metrics=metrics,
            notes=notes,
        )

    # ---------------------------------------------------------------- metrics
    def _metrics(
        self,
        scenario: Scenario,
        context: EvalContext,
        analysis_ms: float,
        generation_ms: float,
    ) -> list[MetricValue]:
        commands = [
            command
            for skill in context.skills
            for block in fenced_code_blocks(skill.bundle.body)
            for command in block.content.splitlines()
            if command.strip() and not command.strip().startswith("#")
        ]
        evidenced = 0
        for command in commands:
            owning = next(
                (skill for skill in context.skills if command.strip() in skill.bundle.body),
                None,
            )
            if self._has_evidence(command, context, owning):
                evidenced += 1
        ratio = (evidenced / len(commands)) if commands else 0.0
        validation_errors = sum(len(result.errors) for result in context.validations.values())
        dangerous = sum(1 for command in commands if classify_command(command).is_dangerous)
        body_tokens = [estimate_tokens(skill.bundle.body) for skill in context.skills]

        metrics = [
            MetricValue.measured_value("skills_generated", len(context.skills), method="pipeline"),
            MetricValue.measured_value(
                "commands_documented", len(commands), method="SKILL.md code blocks"
            ),
            MetricValue.measured_value(
                "commands_with_provenance",
                round(ratio * 100, 1),
                unit="%",
                method="matched against analysis commands",
            ),
            MetricValue.measured_value("validation_errors", validation_errors, method="validator"),
            MetricValue.measured_value(
                "dangerous_commands_embedded", dangerous, method="risk classifier"
            ),
            MetricValue.measured_value(
                "max_skill_md_tokens",
                max(body_tokens) if body_tokens else 0,
                unit="tokens (estimated)",
                method="chars/4 estimate",
            ),
            MetricValue.measured_value(
                "analysis_latency", round(analysis_ms, 1), unit="ms", method="wall clock"
            ),
            MetricValue.measured_value(
                "generation_latency", round(generation_ms, 1), unit="ms", method="wall clock"
            ),
        ]
        metrics.extend(self._agent_metrics(scenario))
        return metrics

    def _agent_metrics(self, scenario: Scenario) -> list[MetricValue]:
        with_skill = self._executor.run(scenario.id, scenario.task, CONDITION_WITH)
        without_skill = self._executor.run(scenario.id, scenario.task, CONDITION_WITHOUT)
        metrics: list[MetricValue] = []
        for result in (without_skill, with_skill):
            label = result.condition
            if result.measured and result.success is not None:
                metrics.append(
                    MetricValue.measured_value(
                        f"task_success[{label}]",
                        1.0 if result.success else 0.0,
                        unit="0/1",
                        method=f"agent {result.agent or self._executor.id}",
                        note=result.notes,
                    )
                )
            else:
                metrics.append(
                    MetricValue.not_measured(
                        f"task_success[{label}]",
                        method=f"agent {self._executor.id}",
                        note=result.notes or "no agent execution in this release",
                    )
                )
            if result.measured and result.tokens is not None:
                metrics.append(
                    MetricValue.measured_value(
                        f"tokens_consumed[{label}]",
                        float(result.tokens),
                        unit="tokens",
                        method=f"agent {result.agent or self._executor.id}",
                    )
                )
            else:
                metrics.append(
                    MetricValue.not_measured(f"tokens_consumed[{label}]", method="agent run")
                )
            if result.measured and result.tool_calls is not None:
                metrics.append(
                    MetricValue.measured_value(
                        f"tool_calls[{label}]",
                        float(result.tool_calls),
                        method=f"agent {result.agent or self._executor.id}",
                    )
                )
            else:
                metrics.append(MetricValue.not_measured(f"tool_calls[{label}]", method="agent run"))
            if result.measured and result.latency_seconds is not None:
                metrics.append(
                    MetricValue.measured_value(
                        f"latency[{label}]",
                        float(result.latency_seconds),
                        unit="s",
                        method=f"agent {result.agent or self._executor.id}",
                    )
                )
            else:
                metrics.append(MetricValue.not_measured(f"latency[{label}]", method="agent run"))
        return metrics

    @staticmethod
    def _has_evidence(command: str, context: EvalContext, skill: GeneratedSkill | None) -> bool:
        needle = " ".join(command.split()).lower()
        if skill is not None:
            tokens = needle.replace("'", " ").replace('"', " ").split()
            if any(token.lstrip("./") in skill.bundle.files for token in tokens):
                return True  # command runs a script shipped with the skill
        for known in context.profile.commands:
            candidate = " ".join(known.command.split()).lower()
            if needle == candidate or needle.startswith(candidate + " "):
                return bool(known.evidence)
        return False

    # ------------------------------------------------------------ comparisons
    def _comparisons(self, results: list[ScenarioResult]) -> list[ComparisonRow]:
        rows: list[ComparisonRow] = []
        metric_names = [
            "task_success",
            "tokens_consumed",
            "tool_calls",
            "latency",
        ]
        for name in metric_names:
            without = _collect(results, f"{name}[{CONDITION_WITHOUT}]")
            with_skill = _collect(results, f"{name}[{CONDITION_WITH}]")
            rows.append(
                ComparisonRow(
                    metric=name,
                    without_skill=without,
                    with_skill=with_skill,
                    note=(
                        "requires an agent executor or a recorded run; "
                        "SkillForge does not run agents in v0.1"
                    ),
                )
            )
        rows.append(
            ComparisonRow(
                metric="validation_errors",
                without_skill=MetricValue.not_measured("validation_errors[without-skill]"),
                with_skill=_collect(results, "validation_errors"),
                note="the without-skill value has no equivalent: there is no skill to validate",
            )
        )
        return rows


def _collect(results: list[ScenarioResult], metric_name: str) -> MetricValue:
    """Aggregate a metric across scenarios (averaging measured values)."""
    values: list[float] = []
    measured = False
    method = ""
    for result in results:
        for metric in result.metrics:
            if metric.name != metric_name:
                continue
            if metric.measured and metric.value is not None:
                measured = True
                values.append(float(metric.value))
                method = metric.method
    if not measured or not values:
        return MetricValue.not_measured(metric_name, method=method or "agent run")
    return MetricValue.measured_value(
        metric_name,
        round(sum(values) / len(values), 3),
        method=method,
        note=f"mean over {len(values)} scenario(s)",
    )
