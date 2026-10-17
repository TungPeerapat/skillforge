"""Unit tests for execution-plan construction."""

from __future__ import annotations

from pathlib import Path

import pytest

from skillforge.analyzer import analyze_repository
from skillforge.config import load_settings
from skillforge.errors import AnalysisError
from skillforge.learning.planning import PlanOptions, build_execution_plan
from skillforge.models import (
    Certainty,
    Command,
    Evidence,
    ExecutionPhase,
    RepositoryProfile,
    RiskLevel,
    StepAction,
)
from skillforge.models.workflow import CommandPurpose, CommandSource


@pytest.fixture
def learnable_profile(copy_fixture, tmp_path: Path) -> tuple[RepositoryProfile, dict[str, str]]:
    repo = copy_fixture("learnable-app")
    settings = load_settings(repo, env={})
    analysis = analyze_repository(repo, settings)
    return analysis.profile, analysis.scan.contents


def _command(
    text: str,
    *,
    purpose: CommandPurpose = CommandPurpose.RUN,
    source: CommandSource = CommandSource.README,
    risk: RiskLevel = RiskLevel.SAFE,
    cwd: str = ".",
) -> Command:
    return Command(
        command=text,
        purpose=purpose,
        source=source,
        cwd=cwd,
        risk=risk,
        certainty=Certainty.FACT,
        evidence=[Evidence(source="README.md", weight=0.8)],
    )


def test_plan_orders_phases_and_keeps_evidence(
    learnable_profile: tuple[RepositoryProfile, dict[str, str]],
) -> None:
    profile, contents = learnable_profile
    plan = build_execution_plan(profile, scan_contents=contents)
    phases = [step.phase for step in plan.steps]
    assert phases[-2:] == [ExecutionPhase.VERIFICATION, ExecutionPhase.CLEANUP]

    launch = plan.step("launch-01")
    assert launch is not None
    assert launch.command == "python -m app"
    assert launch.argv == ["python", "-m", "app"]
    assert launch.long_running
    assert launch.evidence
    assert launch.readiness is not None
    assert launch.readiness.path == "/health"

    verify = plan.step("verify-01")
    assert verify is not None
    assert verify.action is StepAction.HTTP_GET
    assert not verify.executable  # synthetic check, not a shell command

    assert plan.risk_summary["SAFE"] >= 1
    assert plan.risk_summary["DANGEROUS"] == 0
    assert plan.id.startswith("plan-")
    assert plan.fingerprint is not None
    assert plan.fingerprint.workflow_fingerprint


def test_plan_is_stable_for_the_same_profile(
    learnable_profile: tuple[RepositoryProfile, dict[str, str]],
) -> None:
    profile, contents = learnable_profile
    first = build_execution_plan(profile, scan_contents=contents)
    second = build_execution_plan(profile, scan_contents=contents)
    assert first.id == second.id
    assert first.fingerprint == second.fingerprint


def test_dangerous_commands_are_excluded(
    learnable_profile: tuple[RepositoryProfile, dict[str, str]],
) -> None:
    profile, contents = learnable_profile
    profile.commands.append(
        _command("rm -rf /", risk=RiskLevel.DANGEROUS, source=CommandSource.MAKEFILE)
    )
    plan = build_execution_plan(profile, scan_contents=contents)
    assert "rm -rf /" in plan.excluded_dangerous
    for step in plan.steps:
        assert step.command != "rm -rf /"
        assert step.risk is not RiskLevel.DANGEROUS


def test_shell_syntax_commands_are_not_scheduled(
    learnable_profile: tuple[RepositoryProfile, dict[str, str]],
) -> None:
    profile, contents = learnable_profile
    profile.commands.append(
        _command("pip install -e . && python -m app", purpose=CommandPurpose.SETUP)
    )
    plan = build_execution_plan(profile, scan_contents=contents)
    assert any("shell syntax" in note for note in plan.notes)
    for step in plan.steps:
        assert "&&" not in step.command


def test_only_safe_skips_review_commands(
    learnable_profile: tuple[RepositoryProfile, dict[str, str]],
) -> None:
    profile, contents = learnable_profile
    plan = build_execution_plan(
        profile, scan_contents=contents, options=PlanOptions(only_safe=True)
    )
    for step in plan.steps:
        if step.action is StepAction.COMMAND:
            assert step.risk.is_safe
    assert any("--only-safe" in note for note in plan.notes)


def test_missing_launch_command_is_a_clear_error() -> None:
    from datetime import UTC, datetime

    profile = RepositoryProfile(generated_at=datetime.now(UTC), name="empty", root_path=".")
    with pytest.raises(AnalysisError):
        build_execution_plan(profile, scan_contents={})


def test_unknown_target_is_rejected(
    learnable_profile: tuple[RepositoryProfile, dict[str, str]],
) -> None:
    profile, contents = learnable_profile
    with pytest.raises(AnalysisError):
        build_execution_plan(profile, scan_contents=contents, target="test-runner")


def test_detached_compose_service_steps_prefer_specific_services(
    learnable_profile: tuple[RepositoryProfile, dict[str, str]],
) -> None:
    profile, contents = learnable_profile
    profile.commands.append(_command("docker compose up", risk=RiskLevel.REVIEW))
    profile.commands.append(_command("docker compose up -d postgres", risk=RiskLevel.REVIEW))
    plan = build_execution_plan(profile, scan_contents=contents)
    service_steps = [step for step in plan.steps if step.phase is ExecutionPhase.SERVICES]
    assert service_steps
    assert service_steps[0].command == "docker compose up -d postgres"
    assert service_steps[0].risk is RiskLevel.REVIEW


def test_foreground_compose_is_not_used_as_launch(
    learnable_profile: tuple[RepositoryProfile, dict[str, str]],
) -> None:
    profile, contents = learnable_profile
    plan = build_execution_plan(profile, scan_contents=contents)
    launch = plan.step("launch-01")
    assert launch is not None
    assert "compose" not in launch.command
