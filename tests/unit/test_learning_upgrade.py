"""Unit tests for upgrading a skill with verified knowledge."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from skillforge.errors import NotFoundError, SkillExistsError
from skillforge.learning.generalize import generalize
from skillforge.learning.upgrade import upgrade_skill
from skillforge.models import (
    MANIFEST_FILENAME,
    AttemptStatus,
    ExecutionAttempt,
    ExecutionPhase,
    ExecutionPlan,
    ExecutionStep,
    LearningSession,
    ReadinessConfidence,
    ReadinessKind,
    ReadinessPlan,
    RepositoryFingerprint,
    SessionStatus,
    SkillBundle,
    SkillFile,
    SkillManifest,
    SkillMetadata,
    StepAction,
    VerificationResult,
    VerificationStatus,
)
from skillforge.skills.store import read_bundle, write_bundle
from skillforge.utils.fs import sha256_text


def _plan() -> ExecutionPlan:
    readiness = ReadinessPlan(
        kind=ReadinessKind.HTTP,
        confidence=ReadinessConfidence.EVIDENCED,
        url="http://127.0.0.1:8123/health",
        host="127.0.0.1",
        port=8123,
        path="/health",
    )
    return ExecutionPlan(
        id="plan-test",
        target="project-runner",
        repository="demo",
        root_path=".",
        created_at=datetime.now(UTC),
        fingerprint=RepositoryFingerprint(workflow_fingerprint="wf", file_hashes={}),
        steps=[
            ExecutionStep(
                id="launch-01",
                phase=ExecutionPhase.LAUNCH,
                purpose="run",
                action=StepAction.COMMAND,
                command="python -m app",
                argv=["python", "-m", "app"],
                long_running=True,
                readiness=readiness,
            ),
            ExecutionStep(
                id="verify-01",
                phase=ExecutionPhase.VERIFICATION,
                purpose="verify",
                action=StepAction.HTTP_GET,
                readiness=readiness,
            ),
            ExecutionStep(
                id="cleanup-01",
                phase=ExecutionPhase.CLEANUP,
                purpose="stop",
                action=StepAction.TERMINATE,
            ),
        ],
    )


def _session(repo_root: Path) -> LearningSession:
    now = datetime.now(UTC)
    return LearningSession(
        id="learn-20261003T120000Z-abcdef",
        target="project-runner",
        repository="demo",
        root_path=str(repo_root),
        created_at=now,
        completed_at=now,
        status=SessionStatus.VERIFIED,
        plan=_plan(),
        approved=True,
        approval_mode="yes",
        attempts=[
            ExecutionAttempt(
                id="attempt-launch-01-1",
                step_id="launch-01",
                step_purpose="run",
                command="python -m app",
                status=AttemptStatus.SUCCEEDED,
                started_at=now,
                ended_at=now,
            )
        ],
        verification=VerificationResult(
            success=True,
            kind=ReadinessKind.HTTP,
            confidence=ReadinessConfidence.EVIDENCED,
            target="http://127.0.0.1:8123/health",
            status_code=200,
            detail="GET returned 200",
            observed_at=now,
        ),
    )


def _write_base_skill(root: Path) -> Path:
    skill_dir = root / ".skills" / "project-runner"
    bundle = SkillBundle(
        metadata=SkillMetadata(
            name="project-runner",
            description="Start and verify the demo application locally.",
        ),
        body="# Project runner\n\nStart it.\n",
        files={
            "references/evidence.md": SkillFile(
                path="references/evidence.md", content="# Evidence\n\nfrom Procfile:L1\n"
            )
        },
    )
    manifest = SkillManifest(
        skill="project-runner",
        title="Project runner",
        generated_at=datetime.now(UTC),
        source_repository="demo",
    )
    write_bundle(bundle, skill_dir, manifest=manifest)
    return skill_dir


def test_upgrade_adds_verified_artifacts_and_manifest_state(tmp_path: Path) -> None:
    skill_dir = _write_base_skill(tmp_path)
    session = _session(tmp_path)
    workflow = generalize(session)
    result = upgrade_skill(skill_dir, session=session, workflow=workflow)
    assert result.status is VerificationStatus.VERIFIED
    assert "references/verified-workflow.md" in result.created_files

    stored = read_bundle(skill_dir)
    assert stored.manifest is not None
    verification = stored.manifest.verification
    assert verification.status is VerificationStatus.VERIFIED
    assert verification.method == "execution"
    assert verification.session_id == session.id
    assert verification.fingerprint is not None
    assert "references/verified-workflow.md" in stored.bundle.files
    assert "## Verification" in stored.bundle.body
    assert "references/verified-workflow.md" in stored.bundle.body

    contents = stored.bundle.all_contents()
    contents["SKILL.md"] = stored.raw_skill_md
    for path, recorded in stored.manifest.file_hashes.items():
        assert sha256_text(contents[path]) == recorded, path


def test_upgrade_is_idempotent(tmp_path: Path) -> None:
    skill_dir = _write_base_skill(tmp_path)
    session = _session(tmp_path)
    workflow = generalize(session)
    upgrade_skill(skill_dir, session=session, workflow=workflow)
    first = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    upgrade_skill(skill_dir, session=session, workflow=workflow)
    second = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert first == second
    assert second.count("## Verification") == 1
    stored = read_bundle(skill_dir)
    assert stored.manifest is not None
    contents = stored.bundle.all_contents()
    contents["SKILL.md"] = stored.raw_skill_md
    for path, recorded in stored.manifest.file_hashes.items():
        assert sha256_text(contents[path]) == recorded


def test_upgrade_refuses_foreign_skill_without_force(tmp_path: Path) -> None:
    skill_dir = tmp_path / "project-runner"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: project-runner\ndescription: Hand written skill for the demo.\n---\n\n# X\n",
        encoding="utf-8",
    )
    with pytest.raises(SkillExistsError):
        upgrade_skill(
            skill_dir, session=_session(tmp_path), workflow=generalize(_session(tmp_path))
        )


def test_upgrade_force_creates_manifest_for_foreign_skill(tmp_path: Path) -> None:
    skill_dir = tmp_path / "project-runner"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: project-runner\ndescription: Hand written skill for the demo.\n---\n\n# X\n",
        encoding="utf-8",
    )
    session = _session(tmp_path)
    result = upgrade_skill(skill_dir, session=session, workflow=generalize(session), force=True)
    assert result.status is VerificationStatus.VERIFIED
    assert (skill_dir / MANIFEST_FILENAME).is_file()
    stored = read_bundle(skill_dir)
    assert stored.manifest is not None
    assert stored.manifest.verification.status is VerificationStatus.VERIFIED


def test_upgrade_missing_skill_fails_with_hint(tmp_path: Path) -> None:
    session = _session(tmp_path)
    with pytest.raises(NotFoundError):
        upgrade_skill(
            tmp_path / ".skills" / "project-runner",
            session=session,
            workflow=generalize(session),
        )
