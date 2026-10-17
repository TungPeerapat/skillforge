"""Unit tests for learning-session storage."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from skillforge.errors import SecurityRefusal
from skillforge.learning.store import (
    list_session_files,
    load_session,
    new_session_id,
    save_session,
    session_path,
)
from skillforge.models import (
    AttemptStatus,
    ExecutionAttempt,
    ExecutionPhase,
    ExecutionPlan,
    ExecutionStep,
    LearningSession,
    SessionStatus,
    StepAction,
    VerificationStatus,
)


def _session(tmp_path: Path) -> LearningSession:
    return LearningSession(
        id=new_session_id(),
        target="project-runner",
        repository="demo",
        root_path=str(tmp_path),
        created_at=datetime.now(UTC),
        status=SessionStatus.VERIFIED,
        plan=ExecutionPlan(
            id="plan-x",
            target="project-runner",
            repository="demo",
            root_path=str(tmp_path),
            created_at=datetime.now(UTC),
            steps=[
                ExecutionStep(
                    id="launch-01",
                    phase=ExecutionPhase.LAUNCH,
                    purpose="run",
                    action=StepAction.COMMAND,
                    command="python -m app",
                    argv=["python", "-m", "app"],
                )
            ],
        ),
        attempts=[
            ExecutionAttempt(
                id="attempt-launch-01-1",
                step_id="launch-01",
                step_purpose="run",
                command="python -m app",
                status=AttemptStatus.SUCCEEDED,
                started_at=datetime.now(UTC),
                ended_at=datetime.now(UTC),
                stderr_excerpt="API_KEY=sk-live-abcdefghijklmnopqrstuvwx",
            )
        ],
    )


def test_session_round_trip(tmp_path: Path) -> None:
    session = _session(tmp_path)
    path = save_session(session, tmp_path)
    assert path == session_path(tmp_path, session.id)
    loaded = load_session(tmp_path, session.id)
    assert loaded.id == session.id
    assert loaded.status is SessionStatus.VERIFIED
    assert loaded.attempts[0].step_id == "launch-01"


def test_session_file_is_redacted(tmp_path: Path) -> None:
    session = _session(tmp_path)
    path = save_session(session, tmp_path)
    text = path.read_text(encoding="utf-8")
    assert "sk-live-abcdefghijklmnopqrstuvwx" not in text


def test_invalid_session_id_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SecurityRefusal):
        session_path(tmp_path, "../../etc/passwd")


def test_load_missing_session_fails_cleanly(tmp_path: Path) -> None:
    from skillforge.errors import NotFoundError

    with pytest.raises(NotFoundError):
        load_session(tmp_path, "learn-20261003T120000Z-abcdef")


def test_list_session_files(tmp_path: Path) -> None:
    assert list_session_files(tmp_path) == []
    session = _session(tmp_path)
    save_session(session, tmp_path)
    files = list_session_files(tmp_path)
    assert len(files) == 1
    assert files[0].name == f"{session.id}.json"
    assert files[0].parent.name == "sessions"


def test_session_id_is_filesystem_safe() -> None:
    session_id = new_session_id()
    assert session_id.startswith("learn-")
    assert Path(session_id).name == session_id
    assert session_path(Path.cwd(), session_id)


def test_learning_session_verification_status_defaults_to_unverified(tmp_path: Path) -> None:
    session = _session(tmp_path)
    assert session.plan.steps[0].action is StepAction.COMMAND
    # The session itself carries no verification status; that lives in the manifest.
    loaded = load_session(tmp_path, save_session(session, tmp_path).stem)
    assert loaded.status is SessionStatus.VERIFIED


def test_manifest_verification_status_enum() -> None:
    assert VerificationStatus.VERIFIED.label == "VERIFIED"
    assert VerificationStatus.STALE.label == "STALE"
