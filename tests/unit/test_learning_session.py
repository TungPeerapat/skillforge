"""Unit tests for the learning session engine (scripted backend)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from skillforge.execution import ExecutionBackend, ExecutionOutcome, ExecutionRequest
from skillforge.learning.session import DryRunSession, LearningSessionRunner
from skillforge.models import (
    AttemptStatus,
    ExecutionPhase,
    ExecutionPlan,
    ExecutionStep,
    LearningSession,
    ReadinessKind,
    RiskLevel,
    SessionStatus,
    StepAction,
    VerificationResult,
)


class FakeProcess:
    """Minimal ManagedProcessLike used to script long-running steps."""

    def __init__(
        self, *, alive: bool = True, returncode: int | None = None, logs: str = ""
    ) -> None:
        self.pid = 999
        self._alive = alive
        self._returncode = returncode
        self._logs = logs
        self.terminated = False

    @property
    def returncode(self) -> int | None:
        return None if self._alive else self._returncode

    def is_running(self) -> bool:
        return self._alive

    def wait(self, timeout: float) -> int | None:
        return None if self._alive else self._returncode

    def stdout_text(self) -> str:
        return self._logs

    def stderr_text(self) -> str:
        return ""

    def logs_text(self) -> str:
        return self._logs

    @property
    def truncated(self) -> bool:
        return False

    def terminate(self, *, grace: float = 3.0) -> bool:
        self.terminated = True
        self._alive = False
        return True


class ScriptedBackend(ExecutionBackend):
    id = "scripted"

    def __init__(
        self,
        *,
        run_results: list[ExecutionOutcome] | None = None,
        start_results: list[FakeProcess] | None = None,
        verify_results: list[VerificationResult] | None = None,
        start_exception: BaseException | None = None,
    ) -> None:
        self.run_results = list(run_results or [])
        self.start_results = list(start_results or [])
        self.verify_results = list(verify_results or [])
        self.start_exception = start_exception
        self.cleaned = False
        self.started: list[tuple[str, ...]] = []

    def run(self, request: ExecutionRequest) -> ExecutionOutcome:
        if self.run_results:
            return self.run_results.pop(0)
        return ExecutionOutcome(argv=request.argv, cwd=str(request.cwd), exit_code=0)

    def start(self, request: ExecutionRequest) -> FakeProcess:  # type: ignore[override]
        if self.start_exception is not None:
            raise self.start_exception
        self.started.append(request.argv)
        return self.start_results.pop(0)

    def verify(self, readiness, process, *, timeout):  # type: ignore[override]
        if self.verify_results:
            return self.verify_results.pop(0)
        return VerificationResult(
            success=True,
            kind=ReadinessKind.PROCESS,
            observed_at=datetime.now(UTC),
        )

    def cleanup(self) -> None:
        self.cleaned = True

    def describe(self) -> str:
        return "scripted backend for tests"


def _launch_plan() -> ExecutionPlan:
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
                command="python -m app",
                argv=["python", "-m", "app"],
                risk=RiskLevel.SAFE,
                long_running=True,
            ),
            ExecutionStep(
                id="verify-01",
                phase=ExecutionPhase.VERIFICATION,
                purpose="verify readiness",
                action=StepAction.HTTP_GET,
                risk=RiskLevel.SAFE,
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


def _session() -> LearningSession:
    return LearningSession(
        id="learn-test-session",
        target="project-runner",
        repository="demo",
        root_path=".",
        created_at=datetime.now(UTC),
        plan=_launch_plan(),
    )


def _verify(success: bool, detail: str = "") -> VerificationResult:
    return VerificationResult(
        success=success,
        kind=ReadinessKind.HTTP,
        target="http://127.0.0.1:8000/health",
        detail=detail,
        observed_at=datetime.now(UTC),
    )


def test_retry_then_success_verifies_and_generalizes(tmp_path) -> None:
    backend = ScriptedBackend(
        start_results=[
            FakeProcess(alive=False, returncode=1, logs="connection refused"),
            FakeProcess(alive=True),
        ],
        verify_results=[_verify(False, "process exited"), _verify(True, "GET 200")],
    )
    session = _session()
    runner = LearningSessionRunner(
        session,
        backend=backend,
        repo_root=tmp_path,
        approved=True,
        approval_mode="yes",
        max_attempts=3,
        ready_timeout=1,
    )
    result = runner.run()
    assert result.status is SessionStatus.VERIFIED
    assert [attempt.attempt_number for attempt in result.attempts] == [1, 2]
    assert result.attempts[0].diagnosis is not None
    assert result.attempts[0].diagnosis.retryable
    assert result.learned_workflow is not None
    assert backend.cleaned


def test_manual_diagnosis_stops_without_retry(tmp_path) -> None:
    backend = ScriptedBackend(
        start_results=[FakeProcess(alive=False, returncode=1, logs="missing DATABASE_URL")],
        verify_results=[_verify(False)],
    )
    runner = LearningSessionRunner(
        _session(), backend=backend, repo_root=tmp_path, approved=True, approval_mode="yes"
    )
    result = runner.run()
    assert result.status is SessionStatus.FAILED
    assert len(result.attempts) == 1
    diagnosis = result.attempts[0].diagnosis
    assert diagnosis is not None
    assert not diagnosis.retryable
    assert backend.cleaned


def test_attempts_are_bounded(tmp_path) -> None:
    backend = ScriptedBackend(
        start_results=[
            FakeProcess(alive=False, returncode=1, logs="connection refused"),
            FakeProcess(alive=False, returncode=1, logs="connection refused"),
            FakeProcess(alive=False, returncode=1, logs="connection refused"),
        ],
        verify_results=[_verify(False), _verify(False), _verify(False)],
    )
    runner = LearningSessionRunner(
        _session(),
        backend=backend,
        repo_root=tmp_path,
        approved=True,
        approval_mode="yes",
        max_attempts=2,
    )
    result = runner.run()
    assert result.status is SessionStatus.FAILED
    assert len(result.attempts) == 2


def test_interactive_retry_can_be_declined(tmp_path) -> None:
    backend = ScriptedBackend(
        start_results=[FakeProcess(alive=False, returncode=1, logs="connection refused")],
        verify_results=[_verify(False)],
    )
    runner = LearningSessionRunner(
        _session(),
        backend=backend,
        repo_root=tmp_path,
        approved=True,
        approval_mode="interactive",
        retry_approval=lambda diagnosis, step, next_attempt: False,
    )
    result = runner.run()
    assert result.status is SessionStatus.FAILED
    assert len(result.attempts) == 1


def test_keyboard_interrupt_cleans_up_and_propagates(tmp_path) -> None:
    backend = ScriptedBackend(start_exception=KeyboardInterrupt())
    runner = LearningSessionRunner(
        _session(), backend=backend, repo_root=tmp_path, approved=True, approval_mode="yes"
    )
    with pytest.raises(KeyboardInterrupt):
        runner.run()
    assert backend.cleaned
    assert runner.session.status is SessionStatus.ABORTED


def test_unapproved_session_aborts_without_executing(tmp_path) -> None:
    backend = ScriptedBackend()
    runner = LearningSessionRunner(
        _session(), backend=backend, repo_root=tmp_path, approved=False, approval_mode="interactive"
    )
    result = runner.run()
    assert result.status is SessionStatus.ABORTED
    assert result.attempts == []
    assert not backend.cleaned  # nothing was started, nothing to clean


def test_dry_run_session_is_untouched(tmp_path) -> None:
    session = DryRunSession.mark(_session())
    backend = ScriptedBackend()
    runner = LearningSessionRunner(
        session, backend=backend, repo_root=tmp_path, approved=False, approval_mode="dry-run"
    )
    result = runner.run()
    assert result.status is SessionStatus.DRY_RUN
    assert not backend.cleaned


def test_failed_short_step_records_exit_code(tmp_path) -> None:
    plan = _launch_plan()
    plan.steps[0] = ExecutionStep(
        id="setup-01",
        phase=ExecutionPhase.SETUP,
        purpose="setup",
        action=StepAction.COMMAND,
        command="pip install -e .",
        argv=["pip", "install", "-e", "."],
        risk=RiskLevel.REVIEW,
        long_running=False,
    )
    session = _session()
    session.plan = plan
    backend = ScriptedBackend(
        run_results=[
            ExecutionOutcome(argv=("pip",), cwd=".", exit_code=2, stderr="boom", timed_out=False)
        ]
    )
    runner = LearningSessionRunner(
        session, backend=backend, repo_root=tmp_path, approved=True, approval_mode="yes"
    )
    result = runner.run()
    assert result.status is SessionStatus.FAILED
    attempt = result.attempts[0]
    assert attempt.status is AttemptStatus.FAILED
    assert attempt.exit_code == 2
    assert "boom" in attempt.stderr_excerpt


def test_attempt_excerpts_are_redacted(tmp_path) -> None:
    plan = _launch_plan()
    plan.steps[0] = ExecutionStep(
        id="setup-01",
        phase=ExecutionPhase.SETUP,
        purpose="setup",
        action=StepAction.COMMAND,
        command="python setup.py",
        argv=["python", "setup.py"],
        risk=RiskLevel.SAFE,
    )
    session = _session()
    session.plan = plan
    backend = ScriptedBackend(
        run_results=[
            ExecutionOutcome(
                argv=("python",),
                cwd=".",
                exit_code=1,
                stderr="API_KEY=sk-live-abcdefghijklmnopqrstuvwx failed",
                timed_out=False,
            )
        ]
    )
    runner = LearningSessionRunner(
        session, backend=backend, repo_root=tmp_path, approved=True, approval_mode="yes"
    )
    result = runner.run()
    assert "sk-live-abcdefghijklmnopqrstuvwx" not in result.attempts[0].stderr_excerpt
