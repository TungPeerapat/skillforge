"""Unit tests for generalization, sanitization, and learned references."""

from __future__ import annotations

from datetime import UTC, datetime

from skillforge.learning.generalize import (
    generalize,
    render_troubleshooting,
    render_verified_workflow,
    replace_verification_section,
    sanitize_text,
    verification_section,
)
from skillforge.models import (
    AttemptStatus,
    Diagnosis,
    DiagnosisCategory,
    ExecutionAttempt,
    ExecutionPhase,
    ExecutionPlan,
    ExecutionStep,
    LearningSession,
    ProposedAction,
    ProposedActionKind,
    ReadinessConfidence,
    ReadinessKind,
    ReadinessPlan,
    RiskLevel,
    SessionStatus,
    StepAction,
    VerificationResult,
)


def _readiness() -> ReadinessPlan:
    return ReadinessPlan(
        kind=ReadinessKind.HTTP,
        confidence=ReadinessConfidence.EVIDENCED,
        url="http://127.0.0.1:8123/health",
        host="127.0.0.1",
        port=8123,
        path="/health",
    )


def _plan(*, command: str = "python -m app") -> ExecutionPlan:
    return ExecutionPlan(
        id="plan-test",
        target="project-runner",
        repository="demo",
        root_path=".",
        created_at=datetime.now(UTC),
        steps=[
            ExecutionStep(
                id="launch-01",
                phase=ExecutionPhase.LAUNCH,
                purpose="run",
                action=StepAction.COMMAND,
                command=command,
                argv=command.split(),
                risk=RiskLevel.SAFE,
                long_running=True,
                readiness=_readiness(),
            ),
            ExecutionStep(
                id="verify-01",
                phase=ExecutionPhase.VERIFICATION,
                purpose="verify readiness",
                action=StepAction.HTTP_GET,
                risk=RiskLevel.SAFE,
                readiness=_readiness(),
            ),
            ExecutionStep(
                id="cleanup-01",
                phase=ExecutionPhase.CLEANUP,
                purpose="stop processes",
                action=StepAction.TERMINATE,
                risk=RiskLevel.SAFE,
            ),
        ],
    )


def _attempt(
    number: int, *, status: AttemptStatus, stderr: str = "", diagnosis: Diagnosis | None = None
) -> ExecutionAttempt:
    now = datetime.now(UTC)
    return ExecutionAttempt(
        id=f"attempt-launch-01-{number}",
        step_id="launch-01",
        step_purpose="run",
        command="python -m app",
        risk=RiskLevel.SAFE,
        attempt_number=number,
        status=status,
        started_at=now,
        ended_at=now,
        stderr_excerpt=stderr,
        diagnosis=diagnosis,
    )


def _diagnosis() -> Diagnosis:
    return Diagnosis(
        category=DiagnosisCategory.CONNECTION_REFUSED,
        summary="a dependency service refused the connection",
        suggested_resolution="wait until the dependency is ready, then retry",
        proposed_actions=[
            ProposedAction(
                kind=ProposedActionKind.RETRY_STEP,
                description="retry after a wait",
                risk=RiskLevel.SAFE,
                delay_seconds=2.0,
            )
        ],
    )


def _verified_session(*, command: str = "python -m app") -> LearningSession:
    session = LearningSession(
        id="learn-20261003T120000Z-abcdef",
        target="project-runner",
        repository="demo",
        root_path=".",
        created_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
        status=SessionStatus.VERIFIED,
        plan=_plan(command=command),
        approved=True,
        approval_mode="yes",
        attempts=[
            _attempt(
                1, status=AttemptStatus.FAILED, stderr="connection refused", diagnosis=_diagnosis()
            ),
            _attempt(2, status=AttemptStatus.SUCCEEDED),
        ],
        verification=VerificationResult(
            success=True,
            kind=ReadinessKind.HTTP,
            confidence=ReadinessConfidence.EVIDENCED,
            target="http://127.0.0.1:8123/health",
            status_code=200,
            detail="GET http://127.0.0.1:8123/health returned 200",
            observed_at=datetime.now(UTC),
        ),
    )
    return session


def test_generalize_marks_only_executed_steps_verified() -> None:
    workflow = generalize(_verified_session())
    assert [step.id for step in workflow.steps] == ["launch-01"]
    assert workflow.steps[0].verified
    assert workflow.steps[0].command == "python -m app"
    assert workflow.session_id == "learn-20261003T120000Z-abcdef"


def test_generalize_records_troubleshooting_for_resolved_failures() -> None:
    workflow = generalize(_verified_session())
    assert len(workflow.troubleshooting) == 1
    entry = workflow.troubleshooting[0]
    assert entry.category is DiagnosisCategory.CONNECTION_REFUSED
    assert "attempts 1-2" in entry.evidence
    assert "succeeded" in entry.resolution


def test_generalize_records_readiness_requirement() -> None:
    workflow = generalize(_verified_session())
    assert workflow.readiness
    requirement = workflow.readiness[0]
    assert requirement.verified
    assert "http://127.0.0.1:8123/health" in requirement.description


def test_generalize_removes_machine_specific_commands() -> None:
    session = _verified_session(command=r"C:\Users\Bob\project\.venv\Scripts\python.exe app")
    workflow = generalize(session)
    assert workflow.steps == []
    assert workflow.unverified_steps
    assert "machine-specific" in workflow.unverified_steps[0].prerequisite


def test_sanitize_text_strips_usernames_timestamps_and_secrets() -> None:
    text = (
        r"C:\Users\Bob\project /home/alice/work "
        "at 2026-10-03T12:00:00Z API_KEY=sk-live-abcdefghijklmnopqrstuvwx"
    )
    cleaned = sanitize_text(text)
    assert "Bob" not in cleaned
    assert "alice" not in cleaned
    assert "2026-10-03T12:00:00Z" not in cleaned
    assert "sk-live-abcdefghijklmnopqrstuvwx" not in cleaned


def test_rendered_workflow_has_no_absolute_paths_or_logs() -> None:
    workflow = generalize(_verified_session())
    file = render_verified_workflow(workflow)
    assert "verified" in file.content.lower()
    assert workflow.session_id in file.content
    assert "C:\\" not in file.content
    assert "connection refused" not in file.content  # no raw logs in the workflow


def test_render_troubleshooting_is_optional() -> None:
    workflow = generalize(_verified_session())
    assert render_troubleshooting(workflow) is not None
    workflow.troubleshooting = []
    assert render_troubleshooting(workflow) is None


def test_verification_section_replacement_is_idempotent() -> None:
    workflow = generalize(_verified_session())
    section = verification_section(workflow)
    body = "# Title\n\nSome text.\n"
    once = replace_verification_section(body, section)
    twice = replace_verification_section(once, section)
    assert once.count("## Verification") == 1
    assert once == twice


def test_verification_section_replaces_old_text() -> None:
    workflow = generalize(_verified_session())
    section = verification_section(workflow)
    body = "# Title\n\n## Verification\n\nold text\n\n## Next\n\nbody\n"
    updated = replace_verification_section(body, section)
    assert "old text" not in updated
    assert "## Next" in updated
    assert updated.count("## Verification") == 1
