"""Unit tests for deterministic failure diagnosis."""

from __future__ import annotations

from datetime import UTC, datetime

from skillforge.learning.diagnosis import diagnose
from skillforge.models import (
    AttemptStatus,
    DiagnosisCategory,
    ExecutionAttempt,
    ExecutionPhase,
    ExecutionPlan,
    ExecutionStep,
    ProposedActionKind,
    RiskLevel,
    StepAction,
)


def _attempt(
    *,
    stderr: str = "",
    stdout: str = "",
    exit_code: int | None = 1,
    timed_out: bool = False,
) -> ExecutionAttempt:
    now = datetime.now(UTC)
    status = AttemptStatus.TIMED_OUT if timed_out else AttemptStatus.FAILED
    return ExecutionAttempt(
        id="attempt-x",
        step_id="launch-01",
        step_purpose="run",
        command="python -m app",
        risk=RiskLevel.SAFE,
        attempt_number=1,
        status=status,
        started_at=now,
        ended_at=now,
        exit_code=exit_code,
        timed_out=timed_out,
        stdout_excerpt=stdout,
        stderr_excerpt=stderr,
    )


def _step(*, phase: ExecutionPhase = ExecutionPhase.LAUNCH) -> ExecutionStep:
    return ExecutionStep(
        id="launch-01",
        phase=phase,
        purpose="run",
        action=StepAction.COMMAND,
        command="python -m app",
        argv=["python", "-m", "app"],
        risk=RiskLevel.SAFE,
        evidence=[],
    )


def _plan(steps: list[ExecutionStep]) -> ExecutionPlan:
    return ExecutionPlan(
        id="plan-x",
        target="project-runner",
        repository="demo",
        root_path=".",
        created_at=datetime.now(UTC),
        steps=steps,
    )


def test_connection_refused_is_retryable() -> None:
    step = _step()
    diagnosis = diagnose(
        _attempt(stderr="connection refused: localhost:5432"),
        step=step,
        plan=_plan([step]),
        attempts_used=1,
        max_attempts=3,
    )
    assert diagnosis.category is DiagnosisCategory.CONNECTION_REFUSED
    assert diagnosis.retryable
    retry = diagnosis.proposed_actions[0]
    assert retry.kind is ProposedActionKind.RETRY_STEP
    assert retry.delay_seconds > 0
    assert retry.risk is step.risk


def test_no_retry_when_attempt_budget_is_exhausted() -> None:
    step = _step()
    diagnosis = diagnose(
        _attempt(stderr="connection refused"),
        step=step,
        plan=_plan([step]),
        attempts_used=3,
        max_attempts=3,
    )
    assert not diagnosis.retryable
    assert diagnosis.proposed_actions[0].kind is ProposedActionKind.MANUAL


def test_port_in_use_is_manual() -> None:
    step = _step()
    diagnosis = diagnose(
        _attempt(stderr="[Errno 98] Address already in use"),
        step=step,
        plan=_plan([step]),
        attempts_used=1,
        max_attempts=3,
    )
    assert diagnosis.category is DiagnosisCategory.PORT_IN_USE
    assert not diagnosis.retryable


def test_missing_env_var_is_manual() -> None:
    step = _step()
    diagnosis = diagnose(
        _attempt(stderr="missing required environment variable DATABASE_URL"),
        step=step,
        plan=_plan([step]),
        attempts_used=1,
        max_attempts=3,
    )
    assert diagnosis.category is DiagnosisCategory.MISSING_ENV_VAR
    assert not diagnosis.retryable
    assert "set" in diagnosis.suggested_resolution.lower()


def test_missing_module_proposes_setup_step() -> None:
    setup = ExecutionStep(
        id="setup-01",
        phase=ExecutionPhase.SETUP,
        purpose="setup",
        action=StepAction.COMMAND,
        command="pip install -e .",
        argv=["pip", "install", "-e", "."],
        risk=RiskLevel.REVIEW,
    )
    launch = _step()
    diagnosis = diagnose(
        _attempt(stderr="ModuleNotFoundError: No module named 'fastapi'"),
        step=launch,
        plan=_plan([setup, launch]),
        attempts_used=1,
        max_attempts=3,
    )
    assert diagnosis.category is DiagnosisCategory.MISSING_MODULE
    assert not diagnosis.retryable
    proposed = [
        action
        for action in diagnosis.proposed_actions
        if action.kind is ProposedActionKind.RUN_COMMAND
    ]
    assert proposed and proposed[0].command == "pip install -e ."
    assert proposed[0].step_id == "setup-01"


def test_launch_timeout_mentions_readiness() -> None:
    step = _step(phase=ExecutionPhase.LAUNCH)
    diagnosis = diagnose(
        _attempt(timed_out=True, exit_code=None),
        step=step,
        plan=_plan([step]),
        attempts_used=1,
        max_attempts=3,
    )
    assert diagnosis.category is DiagnosisCategory.TIMEOUT
    assert "readiness" in diagnosis.summary
    assert not diagnosis.retryable


def test_missing_executable_detected() -> None:
    step = _step()
    diagnosis = diagnose(
        _attempt(stderr="mix not found on PATH"),
        step=step,
        plan=_plan([step]),
        attempts_used=1,
        max_attempts=3,
    )
    assert diagnosis.category is DiagnosisCategory.MISSING_EXECUTABLE


def test_permission_denied_detected() -> None:
    step = _step()
    diagnosis = diagnose(
        _attempt(stderr="bash: ./run.sh: Permission denied"),
        step=step,
        plan=_plan([step]),
        attempts_used=1,
        max_attempts=3,
    )
    assert diagnosis.category is DiagnosisCategory.PERMISSION_DENIED


def test_unknown_failure_uses_nonzero_exit() -> None:
    step = _step()
    diagnosis = diagnose(
        _attempt(stderr="something odd happened", exit_code=42),
        step=step,
        plan=_plan([step]),
        attempts_used=1,
        max_attempts=3,
    )
    assert diagnosis.category is DiagnosisCategory.NONZERO_EXIT
    assert diagnosis.confidence <= 0.5


def test_diagnosis_evidence_is_redacted() -> None:
    step = _step()
    diagnosis = diagnose(
        _attempt(
            stderr=(
                "connection refused\n"
                "API_KEY=sk-live-abcdefghijklmnopqrstuvwx\n"
                "DATABASE_PASSWORD=hunter2secretvalue\n"
            )
        ),
        step=step,
        plan=_plan([step]),
        attempts_used=1,
        max_attempts=3,
    )
    joined = "\n".join(diagnosis.evidence_lines)
    assert "sk-live-abcdefghijklmnopqrstuvwx" not in joined
    assert "hunter2secretvalue" not in joined
