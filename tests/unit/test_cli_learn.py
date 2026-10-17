"""CLI tests for ``skillforge learn`` (no execution)."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from skillforge.cli.main import app

runner = CliRunner()


def test_dry_run_executes_nothing_and_writes_nothing(copy_fixture) -> None:
    repo = copy_fixture("learnable-app")
    result = runner.invoke(app, ["learn", "project-runner", str(repo), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "No commands executed" in result.output
    assert "python -m app" in result.output
    assert not (repo / ".skills").exists()
    assert not (repo / ".skillforge").exists()


def test_dry_run_json_shape(copy_fixture) -> None:
    repo = copy_fixture("flaky-dependency-app")
    result = runner.invoke(app, ["learn", "project-runner", str(repo), "--dry-run", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["dry_run"] is True
    assert payload["target"] == "project-runner"
    assert payload["risk_summary"]["DANGEROUS"] == 0
    assert payload["session"]["status"] == "dry_run"


def test_interactive_default_is_no(copy_fixture) -> None:
    repo = copy_fixture("learnable-app")
    result = runner.invoke(app, ["learn", "project-runner", str(repo)])
    assert result.exit_code == 0, result.output
    assert "Continue?" in result.output
    assert not (repo / ".skills").exists()
    assert not (repo / ".skillforge").exists()


def test_json_without_yes_is_refused(copy_fixture) -> None:
    repo = copy_fixture("learnable-app")
    result = runner.invoke(app, ["learn", "project-runner", str(repo), "--json"])
    assert result.exit_code == 2
    assert not (repo / ".skills").exists()


def test_unknown_target_is_rejected(copy_fixture) -> None:
    repo = copy_fixture("learnable-app")
    result = runner.invoke(app, ["learn", "test-runner", str(repo), "--dry-run"])
    assert result.exit_code == 2
    assert "unsupported learning target" in result.output


def test_docker_sandbox_is_rejected(copy_fixture) -> None:
    repo = copy_fixture("learnable-app")
    result = runner.invoke(
        app, ["learn", "project-runner", str(repo), "--dry-run", "--sandbox", "docker"]
    )
    assert result.exit_code == 2
    assert "not implemented" in result.output


def test_path_only_invocation_defaults_to_project_runner(copy_fixture) -> None:
    repo = copy_fixture("learnable-app")
    result = runner.invoke(app, ["learn", str(repo), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "project-runner" in result.output


def test_missing_launch_evidence_fails_cleanly(tmp_path: Path) -> None:
    repo = tmp_path / "empty"
    repo.mkdir()
    result = runner.invoke(app, ["learn", "project-runner", str(repo), "--dry-run"])
    assert result.exit_code == 1
    assert "No runnable entry point" in result.output
