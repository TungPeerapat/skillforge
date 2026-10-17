"""Unit tests for repository fingerprints and staleness detection."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from tests.conftest import write_file

from skillforge.learning.fingerprint import (
    check_staleness,
    repository_fingerprint,
    sha256_file,
    workflow_fingerprint,
)
from skillforge.models import ExecutionPhase, ExecutionStep, RepositoryFingerprint


def _step(command: str, *, phase: ExecutionPhase = ExecutionPhase.LAUNCH) -> ExecutionStep:
    return ExecutionStep(
        id=f"{phase.value}-01",
        phase=phase,
        purpose="run",
        command=command,
        argv=command.split(),
    )


def test_sha256_file_is_stable(tmp_path: Path) -> None:
    path = write_file(tmp_path, "a.txt", "hello")
    first = sha256_file(path)
    assert first is not None
    assert first == sha256_file(path)
    assert sha256_file(tmp_path / "missing.txt") is None


def test_fingerprint_records_workflow_files_and_evidence(tmp_path: Path) -> None:
    write_file(tmp_path, "package.json", "{}")
    write_file(tmp_path, "README.md", "# demo")
    steps = [_step("python -m app")]
    steps[0].evidence = []
    fingerprint = repository_fingerprint(tmp_path, steps, git_commit="abc", git_dirty=False)
    assert "package.json" in fingerprint.file_hashes
    assert fingerprint.git_commit == "abc"
    assert fingerprint.workflow_fingerprint == workflow_fingerprint(steps)


def test_workflow_fingerprint_changes_with_commands() -> None:
    first = workflow_fingerprint([_step("python -m app")])
    second = workflow_fingerprint([_step("python -m app --debug")])
    assert first != second
    assert workflow_fingerprint([_step("python -m app")]) == first


def test_staleness_reports_changed_file(tmp_path: Path) -> None:
    write_file(tmp_path, "Makefile", "run:\n\tpython -m app\n")
    steps = [_step("make run")]
    fingerprint = repository_fingerprint(tmp_path, steps)
    assert not check_staleness(fingerprint, tmp_path).stale

    write_file(tmp_path, "Makefile", "run:\n\tpython -m app --debug\n")
    report = check_staleness(fingerprint, tmp_path)
    assert report.stale
    assert any("Makefile" in reason for reason in report.reasons)


def test_staleness_reports_removed_file(tmp_path: Path) -> None:
    write_file(tmp_path, "compose.yaml", "services: {}\n")
    fingerprint = repository_fingerprint(tmp_path, [_step("python -m app")])
    (tmp_path / "compose.yaml").unlink()
    report = check_staleness(fingerprint, tmp_path)
    assert report.stale
    assert any("removed" in reason for reason in report.reasons)


def test_staleness_reports_commit_change_only_when_clean(tmp_path: Path) -> None:
    fingerprint = RepositoryFingerprint(
        git_commit="old",
        git_dirty=False,
        file_hashes={},
        workflow_fingerprint="wf",
        captured_at=datetime.now(UTC),
    )
    report = check_staleness(fingerprint, tmp_path, current_commit="new", current_dirty=False)
    assert report.stale

    dirty_fingerprint = fingerprint.model_copy(update={"git_dirty": True})
    report = check_staleness(dirty_fingerprint, tmp_path, current_commit="new", current_dirty=True)
    assert not report.stale


def test_staleness_without_fingerprint_is_stale(tmp_path: Path) -> None:
    report = check_staleness(None, tmp_path)
    assert report.stale
    assert "no repository fingerprint" in report.reasons[0]
