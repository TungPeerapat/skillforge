"""Tests for the validator."""

from __future__ import annotations

from pathlib import Path

from tests.conftest import REPO_ROOT, write_file

from skillforge.analyzer import analyze_repository
from skillforge.config import load_settings
from skillforge.generator import SkillGenerator
from skillforge.models import FindingSeverity, SkillBundle, SkillMetadata
from skillforge.skills import write_generated_skill
from skillforge.validator import SkillValidator

FIXTURES = REPO_ROOT / "tests" / "fixtures"


def generate_demo(tmp_path: Path):
    root = FIXTURES / "fastapi-app"
    settings = load_settings(root, env={})
    profile = analyze_repository(root, settings).profile
    skills = SkillGenerator(settings).generate(profile)
    out = tmp_path / "skills"
    for skill in skills:
        write_generated_skill(skill, out, repository=profile.name)
    return out, profile


def bundle_with(body: str, *, name: str = "demo-skill", files: dict[str, str] | None = None):
    bundle = SkillBundle(
        metadata=SkillMetadata(name=name, description="A demo skill used by validator tests."),
        body=body,
    )
    for path, content in (files or {}).items():
        from skillforge.models import SkillFile

        bundle.add_file(SkillFile(path=path, content=content))
    return bundle


def codes(result) -> set[str]:
    return {finding.code for finding in result.findings}


# ----------------------------------------------------------------- happy path


def test_generated_skills_pass_validation(tmp_path: Path) -> None:
    out, profile = generate_demo(tmp_path)
    validator = SkillValidator(profile=profile)
    results = validator.validate_output_dir(out)
    assert results
    for result in results:
        assert result.ok, [f.message for f in result.errors]
        assert "frontmatter" in result.checked
        assert "secrets" in result.checked
        assert "evidence" in result.checked


def test_in_memory_generation_validates_before_write(tmp_path: Path) -> None:
    root = FIXTURES / "fastapi-app"
    settings = load_settings(root, env={})
    profile = analyze_repository(root, settings).profile
    skill = SkillGenerator(settings).generate(profile, names=["project-runner"])[0]
    result = SkillValidator(profile=profile).validate_generated(skill)
    assert result.ok, [finding.message for finding in result.errors]


# ------------------------------------------------------------------- findings


def test_missing_directory_is_an_error(tmp_path: Path) -> None:
    result = SkillValidator().validate_dir(tmp_path / "does-not-exist")
    assert "structure.unreadable" in codes(result)
    assert not result.ok


def test_directory_without_skill_md_is_an_error(tmp_path: Path) -> None:
    target = tmp_path / "broken"
    target.mkdir()
    (target / "README.md").write_text("no skill here")
    result = SkillValidator().validate_dir(target)
    assert not result.ok


def test_invalid_frontmatter(tmp_path: Path) -> None:
    target = tmp_path / "bad-frontmatter"
    target.mkdir()
    (target / "SKILL.md").write_text("---\nname: Bad Name\n---\n# x\n")
    result = SkillValidator().validate_dir(target)
    assert "metadata.invalid" in codes(result) or "metadata.invalid-name" in codes(result)
    assert not result.ok


def test_name_must_match_directory(tmp_path: Path) -> None:
    target = tmp_path / "wrong-dir"
    target.mkdir()
    (target / "SKILL.md").write_text(
        "---\nname: other-name\ndescription: A perfectly valid description for testing.\n---\n"
        "# Title\n\nBody text.\n"
    )
    result = SkillValidator().validate_dir(target)
    assert "metadata.name-directory-mismatch" in codes(result)


def test_broken_reference_is_an_error() -> None:
    bundle = bundle_with("# Title\n\nSee [commands](references/commands.md).\n")
    result = SkillValidator().validate_bundle(bundle)
    assert "references.broken-link" in codes(result)
    assert not result.ok


def test_missing_script_is_an_error() -> None:
    bundle = bundle_with("# Title\n\nRun `scripts/run_steps.py`.\n")
    result = SkillValidator().validate_bundle(bundle)
    assert "references.broken-link" in codes(result)


def test_unreferenced_file_warns() -> None:
    bundle = bundle_with(
        "# Title\n\nNo references here.\n",
        files={"references/extra.md": "# Extra\n"},
    )
    result = SkillValidator().validate_bundle(bundle)
    assert "references.unreferenced-file" in codes(result)


def test_dangerous_command_is_an_error() -> None:
    bundle = bundle_with("# Title\n\n```bash\nrm -rf /\n```\n")
    result = SkillValidator().validate_bundle(bundle)
    assert "security.dangerous-command" in codes(result)
    assert not result.ok


def test_secret_in_reference_is_an_error() -> None:
    bundle = bundle_with(
        "# Title\n\nSee [env](references/environment.md).\n",
        files={"references/environment.md": "API_KEY=AKIA3XY7ZQ4W2LMNPQRS\n"},
    )
    result = SkillValidator().validate_bundle(bundle)
    assert "security.possible-secret" in codes(result)
    assert not result.ok


def test_absolute_path_is_an_error() -> None:
    bundle = bundle_with("# Title\n\nRun from /home/ci/build/project.\n")
    result = SkillValidator().validate_bundle(bundle)
    assert "security.absolute-path" in codes(result)
    assert not result.ok


def test_oversized_body_warns() -> None:
    body = "# Title\n\n" + "\n".join(
        f"- instruction number {index} with details" for index in range(1100)
    )
    bundle = bundle_with(body)
    result = SkillValidator().validate_bundle(bundle)
    findings = codes(result)
    assert "size.body-too-large" in findings
    assert "size.body-tokens" in findings


def test_duplicate_instructions_warn() -> None:
    line = "- Always run the test suite before committing your changes."
    bundle = bundle_with(f"# Title\n\n{line}\n{line}\n")
    result = SkillValidator().validate_bundle(bundle)
    assert "duplicates.repeated-instructions" in codes(result)


def test_unevidenced_command_warns_with_profile() -> None:
    root = FIXTURES / "fastapi-app"
    settings = load_settings(root, env={})
    profile = analyze_repository(root, settings).profile
    bundle = bundle_with("# Title\n\n```bash\nredis-server --daemonize yes\n```\n")
    result = SkillValidator(profile=profile).validate_bundle(bundle)
    assert "evidence.command-without-source" in codes(result)
    assert result.ok  # warning, not error


def test_script_syntax_error_is_an_error() -> None:
    bundle = bundle_with(
        "# Title\n\nSee `scripts/broken.py`.\n",
        files={"scripts/broken.py": "def broken(:\n    pass\n"},
    )
    result = SkillValidator().validate_bundle(bundle)
    assert "scripts.python-syntax" in codes(result)


def test_manifest_tampering_warns(tmp_path: Path) -> None:
    out, profile = generate_demo(tmp_path)
    skill_dir = out / "project-runner"
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        skill_md.read_text(encoding="utf-8") + "\n<!-- edited -->\n", encoding="utf-8"
    )
    result = SkillValidator(profile=profile).validate_dir(skill_dir)
    assert "manifest.file-modified" in codes(result)


def test_strict_mode_flags_missing_manifest(tmp_path: Path) -> None:
    target = tmp_path / "hand-written"
    target.mkdir()
    write_file(
        target,
        "SKILL.md",
        "---\nname: hand-written\ndescription: A hand written skill used for testing purposes.\n---\n# Title\n\nBody.\n",
    )
    lenient = SkillValidator().validate_dir(target)
    strict = SkillValidator(strict=True).validate_dir(target)
    assert "manifest.missing" in codes(lenient)
    assert "manifest.missing" in codes(strict)
    severity = next(f.severity for f in strict.findings if f.code == "manifest.missing")
    assert severity is FindingSeverity.WARNING
