"""Unit tests for the verification validation check family."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from tests.conftest import write_file

from skillforge.models import (
    RepositoryFingerprint,
    RepositoryProfile,
    SkillBundle,
    SkillFile,
    SkillManifest,
    SkillMetadata,
    SkillVerification,
    ValidationResult,
    VerificationStatus,
)
from skillforge.utils.fs import sha256_text
from skillforge.validator.checks import DEFAULT_CHECKS, ValidationTarget, check_verification


def _bundle(*, with_workflow: bool = True) -> SkillBundle:
    files: dict[str, SkillFile] = {}
    if with_workflow:
        files["references/verified-workflow.md"] = SkillFile(
            path="references/verified-workflow.md", content="# Verified workflow\n"
        )
    return SkillBundle(
        metadata=SkillMetadata(
            name="project-runner", description="Start and verify the demo application."
        ),
        body="# Project runner\n",
        files=files,
    )


def _manifest(
    *,
    status: VerificationStatus = VerificationStatus.VERIFIED,
    method: str = "execution",
    session_id: str | None = "learn-20261003T120000Z-abcdef",
    verified_at: datetime | None = None,
    fingerprint: RepositoryFingerprint | None = None,
) -> SkillManifest:
    return SkillManifest(
        skill="project-runner",
        title="Project runner",
        generated_at=datetime.now(UTC),
        source_repository="demo",
        verification=SkillVerification(
            status=status,
            method=method,
            session_id=session_id,
            verified_at=verified_at or datetime.now(UTC),
            fingerprint=fingerprint
            or RepositoryFingerprint(workflow_fingerprint="wf", file_hashes={"README.md": "x"}),
        ),
    )


def _target(bundle: SkillBundle, manifest: SkillManifest) -> ValidationTarget:
    return ValidationTarget(bundle=bundle, target="project-runner", manifest=manifest)


def test_verification_check_is_registered() -> None:
    assert check_verification in DEFAULT_CHECKS


def test_valid_verified_manifest_has_no_errors() -> None:
    result = ValidationResult(target="project-runner")
    check_verification(_target(_bundle(), _manifest()), result)
    assert result.errors == []


def test_verified_without_execution_method_is_an_error() -> None:
    result = ValidationResult(target="project-runner")
    check_verification(_target(_bundle(), _manifest(method="llm")), result)
    assert any(finding.code == "verification.invalid" for finding in result.errors)


def test_verified_without_session_is_an_error() -> None:
    result = ValidationResult(target="project-runner")
    check_verification(_target(_bundle(), _manifest(session_id="")), result)
    assert any(finding.code == "verification.invalid" for finding in result.errors)


def test_verified_without_fingerprint_is_an_error() -> None:
    manifest = _manifest()
    manifest.verification.fingerprint = None
    result = ValidationResult(target="project-runner")
    check_verification(_target(_bundle(), manifest), result)
    assert any(finding.code == "verification.invalid" for finding in result.errors)


def test_verified_without_workflow_reference_is_an_error() -> None:
    result = ValidationResult(target="project-runner")
    check_verification(_target(_bundle(with_workflow=False), _manifest()), result)
    assert any("verified-workflow.md is missing" in finding.message for finding in result.errors)


def test_unverified_manifest_passes_silently() -> None:
    manifest = _manifest(status=VerificationStatus.UNVERIFIED)
    result = ValidationResult(target="project-runner")
    check_verification(_target(_bundle(with_workflow=False), manifest), result)
    assert result.findings == []


def test_transient_states_warn() -> None:
    manifest = _manifest(status=VerificationStatus.LEARNING)
    result = ValidationResult(target="project-runner")
    check_verification(_target(_bundle(), manifest), result)
    assert any(finding.code == "verification.transient-state" for finding in result.warnings)


def test_stale_repository_produces_warning(tmp_path: Path) -> None:
    readme = write_file(tmp_path, "README.md", "version one")
    fingerprint = RepositoryFingerprint(
        workflow_fingerprint="wf",
        file_hashes={"README.md": sha256_text(readme.read_text(encoding="utf-8"))},
    )
    profile = RepositoryProfile(
        generated_at=datetime.now(UTC),
        name="demo",
        root_path=str(tmp_path),
    )
    manifest = _manifest(fingerprint=fingerprint)
    target = ValidationTarget(
        bundle=_bundle(), target="project-runner", manifest=manifest, profile=profile
    )
    result = ValidationResult(target="project-runner")
    check_verification(target, result)
    assert result.errors == []

    write_file(tmp_path, "README.md", "version two")
    result = ValidationResult(target="project-runner")
    check_verification(target, result)
    stale = [finding for finding in result.warnings if finding.code == "verification.stale"]
    assert stale
    assert "README.md" in stale[0].message


def test_verified_claim_never_comes_from_an_llm_only() -> None:
    """A manifest that says VERIFIED must fail validation without execution fields."""
    manifest = SkillManifest(
        skill="project-runner",
        title="Project runner",
        generated_at=datetime.now(UTC),
        verification=SkillVerification(status=VerificationStatus.VERIFIED, method="llm"),
    )
    result = ValidationResult(target="project-runner")
    check_verification(_target(_bundle(), manifest), result)
    assert len(result.errors) >= 2  # method, session, timestamp, fingerprint
