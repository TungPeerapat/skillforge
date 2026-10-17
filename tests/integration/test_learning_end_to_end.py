"""Integration tests for the learning workflow against trusted fixtures.

These tests execute processes, but only commands from fixtures under
``tests/fixtures/`` (copied to temporary directories). They are marked
``execution`` so CI can select or exclude them explicitly, and they are fully
offline: every fixture uses the Python standard library only.
"""

from __future__ import annotations

import json
import socket
import threading
from pathlib import Path

import pytest
from tests.conftest import write_file
from typer.testing import CliRunner

from skillforge.cli.main import app
from skillforge.skills.store import read_bundle

pytestmark = [pytest.mark.integration, pytest.mark.execution]

runner = CliRunner()


def _session_file(repo: Path) -> Path:
    sessions = sorted((repo / ".skillforge" / "sessions").glob("*.json"))
    assert sessions, "no learning session was saved"
    return sessions[-1]


def _session_payload(repo: Path) -> dict:
    return json.loads(_session_file(repo).read_text(encoding="utf-8"))


def test_learn_project_runner_verifies_and_upgrades_skill(copy_fixture) -> None:
    repo = copy_fixture("learnable-app")
    result = runner.invoke(
        app,
        [
            "learn",
            "project-runner",
            str(repo),
            "--yes",
            "--only-safe",
            "--ready-timeout",
            "15",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "VERIFIED" in result.output

    skill_dir = repo / ".skills" / "project-runner"
    stored = read_bundle(skill_dir)
    assert stored.manifest is not None
    verification = stored.manifest.verification
    assert verification.status.value == "verified"
    assert verification.method == "execution"
    assert verification.session_id
    assert verification.fingerprint is not None
    assert (skill_dir / "references" / "verified-workflow.md").is_file()
    assert "## Verification" in stored.bundle.body

    payload = _session_payload(repo)
    assert payload["status"] == "verified"
    assert payload["verification"]["success"] is True
    assert payload["attempts"][0]["step_id"] == "launch-01"

    listed = runner.invoke(app, ["list", str(repo), "--generated", "--json"])
    assert listed.exit_code == 0, listed.output
    verification_payload = json.loads(listed.output)["skills"][0]["verification"]
    assert verification_payload["display"] == "VERIFIED"
    assert verification_payload["stale"] is False

    validated = runner.invoke(app, ["validate", str(repo)])
    assert validated.exit_code == 0, validated.output

    exported = runner.invoke(app, ["export", str(repo), "--to", "claude"])
    assert exported.exit_code == 0, exported.output
    assert (repo / ".claude" / "skills" / "project-runner" / "SKILL.md").is_file()


def test_verified_skill_becomes_stale_when_evidence_changes(copy_fixture) -> None:
    repo = copy_fixture("learnable-app")
    learned = runner.invoke(
        app, ["learn", "project-runner", str(repo), "--yes", "--only-safe", "--ready-timeout", "15"]
    )
    assert learned.exit_code == 0, learned.output

    write_file(
        repo, "README.md", (repo / "README.md").read_text(encoding="utf-8") + "\n<!-- edit -->\n"
    )

    listed = runner.invoke(app, ["list", str(repo), "--generated", "--json"])
    assert listed.exit_code == 0, listed.output
    verification = json.loads(listed.output)["skills"][0]["verification"]
    assert verification["display"] == "STALE"
    assert verification["stale"] is True
    assert any("README.md" in reason for reason in verification["stale_reasons"])


def test_retry_after_transient_dependency_failure(copy_fixture) -> None:
    repo = copy_fixture("flaky-dependency-app")
    marker = repo / "state" / "dependency.ready"

    def create_marker() -> None:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("ok", encoding="utf-8")

    timer = threading.Timer(1.2, create_marker)
    timer.start()
    try:
        result = runner.invoke(
            app,
            [
                "learn",
                "project-runner",
                str(repo),
                "--yes",
                "--only-safe",
                "--ready-timeout",
                "10",
                "--max-attempts",
                "3",
            ],
        )
    finally:
        timer.cancel()
    assert result.exit_code == 0, result.output
    payload = _session_payload(repo)
    assert payload["status"] == "verified"
    assert [attempt["attempt_number"] for attempt in payload["attempts"]] == [1, 2]
    assert payload["attempts"][0]["diagnosis"]["category"] == "connection_refused"
    troubleshooting = repo / ".skills" / "project-runner" / "references" / "troubleshooting.md"
    assert troubleshooting.is_file()
    assert "connection" in troubleshooting.read_text(encoding="utf-8").lower()


def test_readiness_timeout_fails_and_cleans_up(copy_fixture) -> None:
    repo = copy_fixture("silent-app")
    result = runner.invoke(
        app,
        [
            "learn",
            "project-runner",
            str(repo),
            "--yes",
            "--only-safe",
            "--ready-timeout",
            "2",
        ],
    )
    assert result.exit_code == 1
    assert "FAILED" in result.output
    payload = _session_payload(repo)
    assert payload["status"] == "failed"
    assert payload["verification"]["success"] is False
    assert payload["attempts"][0]["timed_out"] is True

    # Nothing was left listening on the documented port.
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", 8125), timeout=1).close()


def test_missing_environment_variable_is_not_auto_retried(copy_fixture) -> None:
    repo = copy_fixture("missing-env-app")
    result = runner.invoke(
        app, ["learn", "project-runner", str(repo), "--yes", "--only-safe", "--ready-timeout", "5"]
    )
    assert result.exit_code == 1
    payload = _session_payload(repo)
    assert payload["status"] == "failed"
    assert len(payload["attempts"]) == 1
    assert payload["attempts"][0]["diagnosis"]["category"] == "missing_env_var"
    assert not (repo / ".skills").exists()


def test_missing_executable_is_diagnosed(copy_fixture) -> None:
    repo = copy_fixture("missing-tool-app")
    result = runner.invoke(
        app, ["learn", "project-runner", str(repo), "--yes", "--only-safe", "--ready-timeout", "5"]
    )
    assert result.exit_code == 1
    payload = _session_payload(repo)
    assert payload["attempts"][0]["diagnosis"]["category"] == "missing_executable"


def test_secrets_never_enter_learning_artifacts(tmp_path: Path) -> None:
    repo = tmp_path / "secret-app"
    secret = "sk-live-abcdefghijklmnopqrstuvwx"
    write_file(repo, "Procfile", "web: python -m secret_app\n")
    write_file(
        repo,
        "README.md",
        "# secret-app\n\n## Run\n\n```bash\npython -m secret_app\n```\n",
    )
    write_file(
        repo,
        "secret_app/__main__.py",
        "import sys\n"
        f"print('API_KEY={secret}', file=sys.stderr, flush=True)\n"
        "raise SystemExit(1)\n",
    )
    write_file(repo, "secret_app/__init__.py", "")

    result = runner.invoke(
        app, ["learn", "project-runner", str(repo), "--yes", "--only-safe", "--ready-timeout", "5"]
    )
    assert result.exit_code == 1
    session_text = _session_file(repo).read_text(encoding="utf-8")
    assert secret not in session_text
    assert "sk-live-" not in session_text
    # Only learning artifacts are inspected: the fixture source itself contains
    # the literal by construction.
    for path in sorted((repo / ".skillforge").rglob("*")):
        if path.is_file():
            assert secret not in path.read_text(encoding="utf-8", errors="replace")
    assert not (repo / ".skills").exists()


def test_dry_run_never_spawns_processes(copy_fixture) -> None:
    repo = copy_fixture("missing-tool-app")
    result = runner.invoke(app, ["learn", "project-runner", str(repo), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "No commands executed" in result.output
    assert not (repo / ".skillforge").exists()
    assert not (repo / ".skills").exists()


def test_learn_reuses_existing_generated_skill(copy_fixture) -> None:
    repo = copy_fixture("learnable-app")
    generated = runner.invoke(app, ["generate", str(repo)])
    assert generated.exit_code == 0, generated.output
    before = read_bundle(repo / ".skills" / "project-runner")
    assert before.manifest is not None
    assert before.manifest.verification.status.value == "unverified"

    learned = runner.invoke(
        app, ["learn", "project-runner", str(repo), "--yes", "--only-safe", "--ready-timeout", "15"]
    )
    assert learned.exit_code == 0, learned.output
    after = read_bundle(repo / ".skills" / "project-runner")
    assert after.manifest is not None
    assert after.manifest.verification.status.value == "verified"
    # No duplicate skill directory was created.
    assert len([child for child in (repo / ".skills").iterdir() if child.is_dir()]) == 1
