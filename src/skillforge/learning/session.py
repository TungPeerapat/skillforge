"""Learning-session orchestration.

The session engine is deterministic: it executes the approved plan, records
every attempt, diagnoses failures with rules, and retries only within explicit,
bounded consent. Cleanup always runs — success, failure, timeout, exception, or
Ctrl+C.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from skillforge.execution import ExecutionBackend, ExecutionOutcome, ExecutionRequest
from skillforge.execution.readiness import process_only_plan
from skillforge.learning.diagnosis import diagnose
from skillforge.learning.generalize import generalize
from skillforge.logging import get_logger
from skillforge.models import (
    DEFAULT_OUTPUT_LIMIT_BYTES,
    MAX_SESSION_ATTEMPTS,
    AttemptStatus,
    Diagnosis,
    ExecutionAttempt,
    ExecutionStep,
    LearningSession,
    ProposedActionKind,
    SessionStatus,
    StepAction,
)
from skillforge.security.redaction import redact_text

logger = get_logger("learning.session")

#: How many characters of a stream excerpt are stored in a session record.
_EXCERPT_LIMIT = 4000

#: Callback asked before an automatic retry. Returns True to continue.
RetryApproval = Callable[[Diagnosis, ExecutionStep, int], bool]


class LearningSessionRunner:
    """Run an approved :class:`ExecutionPlan` and record what happened."""

    def __init__(
        self,
        session: LearningSession,
        *,
        backend: ExecutionBackend,
        repo_root: Path,
        approved: bool,
        approval_mode: str,
        max_attempts: int = 3,
        ready_timeout: float = 30.0,
        retry_approval: RetryApproval | None = None,
        provider_diagnosis: Callable[
            [LearningSession, ExecutionAttempt, ExecutionStep, Diagnosis], Diagnosis | None
        ]
        | None = None,
    ) -> None:
        self.session = session
        self._backend = backend
        self._repo_root = repo_root.resolve()
        self._approved = approved
        self._approval_mode = approval_mode
        self._max_attempts = max(1, min(max_attempts, 5))
        self._ready_timeout = max(1.0, ready_timeout)
        self._retry_approval = retry_approval
        self._provider_diagnosis = provider_diagnosis

    # -------------------------------------------------------------------- run
    def run(self) -> LearningSession:
        session = self.session
        if session.status is SessionStatus.DRY_RUN:
            return session
        if not self._approved:
            session.status = SessionStatus.ABORTED
            session.completed_at = datetime.now(UTC)
            session.notes.append("execution was not approved; nothing ran")
            return session

        session.status = SessionStatus.EXECUTING
        session.approved = True
        session.approval_mode = self._approval_mode
        try:
            for step in session.plan.steps:
                if self._session_attempts >= MAX_SESSION_ATTEMPTS:
                    session.warnings.append(
                        f"session attempt cap ({MAX_SESSION_ATTEMPTS}) reached; stopping"
                    )
                    break
                if step.excluded:
                    continue
                if step.action is StepAction.COMMAND and not self._run_command_step(step):
                    session.status = SessionStatus.FAILED
                    break
                # Verification/cleanup actions are handled by the launch step.
            else:
                if session.verification is not None and session.verification.success:
                    session.status = SessionStatus.VERIFIED
                else:
                    session.status = SessionStatus.FAILED
                    session.warnings.append(
                        "the plan finished without a successful readiness check"
                    )
        except KeyboardInterrupt:
            session.status = SessionStatus.ABORTED
            session.notes.append("interrupted; started processes were terminated")
            raise
        finally:
            self._backend.cleanup()
            session.completed_at = datetime.now(UTC)

        if session.status is SessionStatus.VERIFIED:
            try:
                session.learned_workflow = generalize(session)
            except ValueError:  # pragma: no cover - defensive
                session.status = SessionStatus.FAILED
                session.warnings.append("could not generalize the verified session")
        return session

    # ------------------------------------------------------------------ steps
    def _run_command_step(self, step: ExecutionStep) -> bool:
        attempts_used = 0
        previous: list[str] = [
            attempt.step_id
            for attempt in self.session.attempts
            if attempt.status is AttemptStatus.SUCCEEDED
        ]
        while True:
            if self._session_attempts >= MAX_SESSION_ATTEMPTS:
                self.session.warnings.append(
                    f"session attempt cap ({MAX_SESSION_ATTEMPTS}) reached"
                )
                return False
            attempts_used += 1
            attempt = self._attempt(step, attempt_number=attempts_used, previous_steps=previous)
            self.session.attempts.append(attempt)
            if attempt.succeeded:
                previous.append(step.id)
                return True
            diagnosis = diagnose(
                attempt,
                step=step,
                plan=self.session.plan,
                attempts_used=attempts_used,
                max_attempts=self._max_attempts,
            )
            attempt.diagnosis = diagnosis
            refined = self._refine(attempt, step, diagnosis)
            attempt.diagnosis = refined
            self.session.warnings.append(
                f"{step.id}: attempt {attempts_used} failed ({refined.category.value})"
            )
            if not self._may_retry(refined, step, attempts_used):
                return False
            delay = self._retry_delay(refined)
            logger.info(
                "retrying step",
                extra={"step": step.id, "attempt": attempts_used, "delay": delay},
            )
            time.sleep(delay)

    @property
    def _session_attempts(self) -> int:
        return len(self.session.attempts)

    def _may_retry(self, diagnosis: Diagnosis, step: ExecutionStep, attempts_used: int) -> bool:
        if attempts_used >= self._max_attempts:
            return False
        if not diagnosis.retryable:
            return False
        actions = [
            action
            for action in diagnosis.proposed_actions
            if action.kind in (ProposedActionKind.RETRY_STEP, ProposedActionKind.WAIT)
        ]
        if not actions:
            return False
        if self._approval_mode == "yes":
            return True
        if self._retry_approval is None:
            return False
        return self._retry_approval(diagnosis, step, attempts_used + 1)

    @staticmethod
    def _retry_delay(diagnosis: Diagnosis) -> float:
        delays = [
            action.delay_seconds
            for action in diagnosis.proposed_actions
            if action.kind in (ProposedActionKind.RETRY_STEP, ProposedActionKind.WAIT)
        ]
        return min(15.0, max(delays) if delays else 2.0)

    def _refine(
        self,
        attempt: ExecutionAttempt,
        step: ExecutionStep,
        diagnosis: Diagnosis,
    ) -> Diagnosis:
        if self._provider_diagnosis is None:
            return diagnosis
        try:
            refined = self._provider_diagnosis(self.session, attempt, step, diagnosis)
        except Exception as exc:  # pragma: no cover - provider failures must not break learning
            self.session.warnings.append(f"LLM diagnosis unavailable: {type(exc).__name__}")
            return diagnosis
        return refined or diagnosis

    # --------------------------------------------------------------- attempts
    def _attempt(
        self, step: ExecutionStep, *, attempt_number: int, previous_steps: list[str]
    ) -> ExecutionAttempt:
        started = datetime.now(UTC)
        started_monotonic = time.monotonic()
        if step.long_running:
            attempt = self._attempt_long_running(
                step, attempt_number=attempt_number, previous_steps=previous_steps, started=started
            )
        else:
            attempt = self._attempt_short(
                step, attempt_number=attempt_number, previous_steps=previous_steps, started=started
            )
        attempt.duration_ms = round((time.monotonic() - started_monotonic) * 1000, 3)
        attempt.ended_at = datetime.now(UTC)
        attempt.environment_keys = []
        attempt.notes.append(f"platform: {sys.platform}")
        attempt.notes.append("environment values are never recorded")
        return attempt

    def _attempt_short(
        self,
        step: ExecutionStep,
        *,
        attempt_number: int,
        previous_steps: list[str],
        started: datetime,
    ) -> ExecutionAttempt:
        request = ExecutionRequest(
            argv=tuple(step.argv),
            cwd=self._repo_root / (step.cwd or "."),
            timeout=step.timeout_seconds,
            output_limit=DEFAULT_OUTPUT_LIMIT_BYTES,
            label=step.purpose,
        )
        try:
            outcome = self._backend.run(request)
        except Exception as exc:  # pragma: no cover - backend bug
            outcome = ExecutionOutcome(
                argv=tuple(step.argv),
                cwd=step.cwd,
                start_error=f"{type(exc).__name__}: {exc}",
            )
        return self._outcome_attempt(
            step,
            outcome,
            attempt_number=attempt_number,
            previous_steps=previous_steps,
            started=started,
            timed_out=outcome.timed_out,
        )

    def _attempt_long_running(
        self,
        step: ExecutionStep,
        *,
        attempt_number: int,
        previous_steps: list[str],
        started: datetime,
    ) -> ExecutionAttempt:
        request = ExecutionRequest(
            argv=tuple(step.argv),
            cwd=self._repo_root / (step.cwd or "."),
            timeout=step.timeout_seconds,
            output_limit=DEFAULT_OUTPUT_LIMIT_BYTES,
            label=step.purpose,
        )
        process = None
        try:
            process = self._backend.start(request)
        except Exception as exc:
            return ExecutionAttempt(
                id=f"attempt-{step.id}-{attempt_number}",
                step_id=step.id,
                step_purpose=step.purpose,
                command=step.command,
                cwd=step.cwd,
                risk=step.risk,
                attempt_number=attempt_number,
                status=AttemptStatus.FAILED,
                started_at=started,
                ended_at=datetime.now(UTC),
                stderr_excerpt=redact_text(str(exc), max_length=_EXCERPT_LIMIT),
                previous_steps=previous_steps,
                notes=[f"could not start: {type(exc).__name__}"],
            )
        if process is None:  # pragma: no cover - defensive
            raise RuntimeError("execution backend returned no process")
        try:
            readiness = step.readiness or process_only_plan()
            result = self._backend.verify(readiness, process, timeout=self._ready_timeout)
            self.session.verification = result
            alive = process.is_running()
            exit_code = None if alive else process.returncode
            # Terminate before reading the logs so the readers finish and every
            # captured line is attributed to this attempt.
            process.terminate(grace=3.0)
            logs_out = redact_text(
                process.stdout_text()[:_EXCERPT_LIMIT], max_length=_EXCERPT_LIMIT
            )
            logs_err = redact_text(
                process.stderr_text()[:_EXCERPT_LIMIT], max_length=_EXCERPT_LIMIT
            )
            status = (
                AttemptStatus.SUCCEEDED
                if result.success
                else (AttemptStatus.TIMED_OUT if alive else AttemptStatus.FAILED)
            )
            note = f"readiness {result.kind.value}: {result.detail}"
            return ExecutionAttempt(
                id=f"attempt-{step.id}-{attempt_number}",
                step_id=step.id,
                step_purpose=step.purpose,
                command=step.command,
                cwd=step.cwd,
                risk=step.risk,
                attempt_number=attempt_number,
                status=status,
                started_at=started,
                ended_at=datetime.now(UTC),
                exit_code=exit_code,
                timed_out=status is AttemptStatus.TIMED_OUT,
                stdout_excerpt=logs_out,
                stderr_excerpt=logs_err,
                output_truncated=process.truncated,
                previous_steps=previous_steps,
                notes=[note, "process was terminated after the readiness check"],
            )
        finally:
            if process is not None:
                process.terminate(grace=3.0)

    def _outcome_attempt(
        self,
        step: ExecutionStep,
        outcome: ExecutionOutcome,
        *,
        attempt_number: int,
        previous_steps: list[str],
        started: datetime,
        timed_out: bool,
    ) -> ExecutionAttempt:
        if outcome.blocked_reason:
            status = AttemptStatus.BLOCKED
        elif outcome.start_error:
            status = AttemptStatus.FAILED
        elif timed_out:
            status = AttemptStatus.TIMED_OUT
        elif outcome.exit_code == 0:
            status = AttemptStatus.SUCCEEDED
        else:
            status = AttemptStatus.FAILED
        stderr = redact_text(outcome.stderr, max_length=_EXCERPT_LIMIT)
        stdout = redact_text(outcome.stdout, max_length=_EXCERPT_LIMIT)
        if outcome.start_error:
            stderr = redact_text(outcome.start_error, max_length=_EXCERPT_LIMIT)
        return ExecutionAttempt(
            id=f"attempt-{step.id}-{attempt_number}",
            step_id=step.id,
            step_purpose=step.purpose,
            command=step.command,
            cwd=step.cwd,
            risk=step.risk,
            attempt_number=attempt_number,
            status=status,
            started_at=started,
            ended_at=datetime.now(UTC),
            exit_code=outcome.exit_code,
            timed_out=timed_out,
            stdout_excerpt=stdout,
            stderr_excerpt=stderr,
            output_truncated=outcome.truncated
            or len(outcome.stdout) > _EXCERPT_LIMIT
            or len(outcome.stderr) > _EXCERPT_LIMIT,
            previous_steps=previous_steps,
            notes=[],
        )


class DryRunSession:
    """A session that was planned but explicitly not executed."""

    @staticmethod
    def mark(session: LearningSession) -> LearningSession:
        session.status = SessionStatus.DRY_RUN
        session.approved = False
        session.completed_at = datetime.now(UTC)
        session.notes.append("dry run: no commands were executed and nothing was written")
        return session


__all__ = ["DryRunSession", "LearningSessionRunner", "RetryApproval"]
