"""Tests for agent exporters."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.conftest import REPO_ROOT

from skillforge.analyzer import analyze_repository
from skillforge.config import load_settings
from skillforge.errors import SkillExistsError, UsageError
from skillforge.exporters import (
    ClaudeCodeExporter,
    CodexExporter,
    OpenCodeExporter,
    PortableExporter,
    exporter_ids,
    get_exporter,
    resolve_targets,
)
from skillforge.generator import SkillGenerator
from skillforge.skills import write_generated_skill
from skillforge.utils.markdown import split_frontmatter

FIXTURES = REPO_ROOT / "tests" / "fixtures"


@pytest.fixture
def generated_skills(tmp_path: Path):
    root = FIXTURES / "fastapi-app"
    settings = load_settings(root, env={})
    profile = analyze_repository(root, settings).profile
    skills = SkillGenerator(settings).generate(profile)
    out = tmp_path / "generated"
    for skill in skills:
        write_generated_skill(
            skill, out, repository=profile.name, git_commit="abc", git_dirty=False
        )
    return out, skills


@pytest.mark.parametrize(
    ("exporter_class", "expected_subdir"),
    [
        (ClaudeCodeExporter, ".claude/skills"),
        (CodexExporter, ".agents/skills"),
        (OpenCodeExporter, ".opencode/skills"),
        (PortableExporter, "skills"),
    ],
)
def test_export_layouts(
    tmp_path: Path, generated_skills, exporter_class, expected_subdir: str
) -> None:
    out, skills = generated_skills
    repo = tmp_path / "repo"
    repo.mkdir()
    exporter = exporter_class()
    exported = exporter.export_directory(out, repo)
    assert sorted(exported) == sorted(skill.name for skill in skills)
    for name in exported:
        skill_dir = repo / expected_subdir / name
        assert (skill_dir / "SKILL.md").is_file()
        assert (skill_dir / ".skillforge.json").is_file()
        parsed = split_frontmatter((skill_dir / "SKILL.md").read_text(encoding="utf-8"))
        assert parsed.metadata is not None
        assert parsed.metadata["name"] == name


def test_export_preserves_supporting_files(tmp_path: Path, generated_skills) -> None:
    out, _skills = generated_skills
    repo = tmp_path / "repo"
    repo.mkdir()
    ClaudeCodeExporter().export_directory(out, repo)
    runner = repo / ".claude" / "skills" / "project-runner"
    assert (runner / "references" / "evidence.md").is_file()
    assert (runner / "scripts" / "run_steps.py").is_file()
    assert (runner / "references" / "architecture.md").is_file()


def test_export_refuses_to_overwrite_without_force(tmp_path: Path, generated_skills) -> None:
    out, _skills = generated_skills
    repo = tmp_path / "repo"
    repo.mkdir()
    exporter = CodexExporter()
    exporter.export_directory(out, repo)
    with pytest.raises(SkillExistsError):
        exporter.export_directory(out, repo)
    # With force it succeeds.
    assert exporter.export_directory(out, repo, force=True)


def test_export_dry_run_does_not_write(tmp_path: Path, generated_skills) -> None:
    _out, _skills = generated_skills
    repo = tmp_path / "repo"
    repo.mkdir()
    root = CodexExporter().skills_root(repo)
    assert not root.exists()


def test_global_scope_uses_home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    exporter = ClaudeCodeExporter(global_scope=True)
    assert exporter.skills_root(tmp_path / "repo") == tmp_path / ".claude" / "skills"
    opencode = OpenCodeExporter(global_scope=True)
    assert opencode.skills_root(tmp_path / "repo") == tmp_path / ".config" / "opencode" / "skills"


def test_resolve_targets() -> None:
    assert resolve_targets("claude") == ["claude"]
    assert resolve_targets("claude-code") == ["claude"]
    assert resolve_targets("all") == exporter_ids()
    assert resolve_targets("claude,codex") == ["claude", "codex"]
    with pytest.raises(UsageError):
        resolve_targets("notepad")


def test_get_exporter_rejects_unknown() -> None:
    with pytest.raises(UsageError):
        get_exporter("unknown-agent")


def test_discover_lists_exported_skills(tmp_path: Path, generated_skills) -> None:
    out, skills = generated_skills
    repo = tmp_path / "repo"
    repo.mkdir()
    exporter = PortableExporter()
    assert exporter.discover(repo) == []
    exporter.export_directory(out, repo)
    assert exporter.discover(repo) == sorted(skill.name for skill in skills)


def test_exporters_document_target_facts() -> None:
    for name in exporter_ids():
        notes = get_exporter(name).compatibility_notes()
        assert notes, f"{name} must document what it knows about the target format"


def test_exported_frontmatter_is_portable(tmp_path: Path, generated_skills) -> None:
    """OpenCode/Codex only recognise name, description, license, compatibility, metadata."""
    out, _skills = generated_skills
    repo = tmp_path / "repo"
    repo.mkdir()
    OpenCodeExporter().export_directory(out, repo)
    for skill_dir in (repo / ".opencode" / "skills").iterdir():
        parsed = split_frontmatter((skill_dir / "SKILL.md").read_text(encoding="utf-8"))
        assert parsed.metadata is not None
        unknown = set(parsed.metadata) - {
            "name",
            "description",
            "license",
            "compatibility",
            "metadata",
        }
        assert not unknown, f"{skill_dir.name} has non-portable frontmatter keys: {unknown}"
