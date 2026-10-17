"""Unit tests for the local execution backend and process management."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from skillforge.errors import SecurityRefusal
from skillforge.execution import ExecutionRequest, LocalExecutionBackend

pytestmark = pytest.mark.execution


def _python(*args: str) -> tuple[str, ...]:
    return (sys.executable, *args)


def test_run_captures_stdout_and_exit_code(tmp_path: Path) -> None:
    backend = LocalExecutionBackend(tmp_path)
    outcome = backend.run(
        ExecutionRequest(argv=_python("-c", "print('hello')"), cwd=tmp_path, timeout=30)
    )
    assert outcome.ok
    assert outcome.exit_code == 0
    assert "hello" in outcome.stdout
    assert outcome.blocked_reason is None


def test_run_reports_failure_exit_code(tmp_path: Path) -> None:
    backend = LocalExecutionBackend(tmp_path)
    outcome = backend.run(
        ExecutionRequest(argv=_python("-c", "import sys; sys.exit(3)"), cwd=tmp_path, timeout=30)
    )
    assert not outcome.ok
    assert outcome.exit_code == 3


def test_run_enforces_timeout(tmp_path: Path) -> None:
    backend = LocalExecutionBackend(tmp_path)
    outcome = backend.run(
        ExecutionRequest(argv=_python("-c", "import time; time.sleep(30)"), cwd=tmp_path, timeout=1)
    )
    assert outcome.timed_out
    assert not outcome.ok


def test_run_limits_output(tmp_path: Path) -> None:
    backend = LocalExecutionBackend(tmp_path)
    outcome = backend.run(
        ExecutionRequest(
            argv=_python("-c", "print('x' * 100000)"),
            cwd=tmp_path,
            timeout=30,
            output_limit=1024,
        )
    )
    assert outcome.ok
    assert outcome.truncated
    assert len(outcome.stdout) <= 2048


def test_missing_executable_is_reported_without_spawning(tmp_path: Path) -> None:
    backend = LocalExecutionBackend(tmp_path)
    outcome = backend.run(
        ExecutionRequest(argv=("definitely-not-a-real-tool-xyz",), cwd=tmp_path, timeout=5)
    )
    assert outcome.start_error is not None
    assert "not found on PATH" in outcome.start_error


def test_start_rejects_missing_executable(tmp_path: Path) -> None:
    backend = LocalExecutionBackend(tmp_path)
    with pytest.raises(FileNotFoundError):
        backend.start(
            ExecutionRequest(argv=("definitely-not-a-real-tool-xyz",), cwd=tmp_path, timeout=5)
        )


def test_working_directory_escape_is_refused(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    backend = LocalExecutionBackend(repo)
    with pytest.raises(SecurityRefusal):
        backend.resolve_cwd("../")
    with pytest.raises(SecurityRefusal):
        backend.resolve_cwd(str(repo.parent))


def test_missing_working_directory_is_refused(tmp_path: Path) -> None:
    backend = LocalExecutionBackend(tmp_path)
    with pytest.raises(SecurityRefusal):
        backend.resolve_cwd("does-not-exist")


def test_run_refuses_cwd_escape_via_outcome(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    backend = LocalExecutionBackend(repo)
    outcome = backend.run(
        ExecutionRequest(argv=_python("-c", "print(1)"), cwd=tmp_path / "elsewhere", timeout=5)
    )
    assert outcome.blocked_reason is not None
    assert not outcome.ok


def test_started_process_is_terminated_and_not_orphaned(tmp_path: Path) -> None:
    backend = LocalExecutionBackend(tmp_path)
    process = backend.start(
        ExecutionRequest(
            argv=_python("-c", "import time; print('up', flush=True); time.sleep(60)"),
            cwd=tmp_path,
            timeout=60,
        )
    )
    assert process.is_running()
    deadline = time.monotonic() + 5
    while "up" not in process.logs_text() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert "up" in process.logs_text()
    backend.cleanup()
    assert not process.is_running()
    assert process.returncode is not None


def test_terminate_is_idempotent(tmp_path: Path) -> None:
    backend = LocalExecutionBackend(tmp_path)
    process = backend.start(
        ExecutionRequest(
            argv=_python("-c", "import time; time.sleep(60)"), cwd=tmp_path, timeout=60
        )
    )
    assert process.terminate()
    assert process.terminate()
    backend.cleanup()


def test_describe_states_no_isolation(tmp_path: Path) -> None:
    description = LocalExecutionBackend(tmp_path).describe()
    assert "not a sandbox" in description
    assert "argv-only" in description


def test_run_kills_process_after_timeout(tmp_path: Path) -> None:
    backend = LocalExecutionBackend(tmp_path)
    started = time.monotonic()
    outcome = backend.run(
        ExecutionRequest(argv=_python("-c", "import time; time.sleep(30)"), cwd=tmp_path, timeout=1)
    )
    elapsed = time.monotonic() - started
    assert outcome.timed_out
    assert elapsed < 15
