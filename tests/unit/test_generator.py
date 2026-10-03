"""Tests for deterministic skill generation."""

from __future__ import annotations

import ast

import pytest
from tests.conftest import REPO_ROOT

from skillforge.analyzer import analyze_repository
from skillforge.config import load_settings
from skillforge.generator import SkillGenerator, build_blueprint, render_skill
from skillforge.generator.renderers import _TEMPLATES
from skillforge.models import GenerationMode, SkillMetadata
from skillforge.planner import SkillPlanner
from skillforge.planner.rules import SUPPORTED_SKILLS
from skillforge.security.command_risk import classify_command
from skillforge.utils.markdown import fenced_code_blocks, split_frontmatter

FIXTURES = REPO_ROOT / "tests" / "fixtures"


def analyze_and_generate(fixture: str, *, names: list[str] | None = None):
    root = FIXTURES / fixture
    settings = load_settings(root, env={})
    profile = analyze_repository(root, settings).profile
    generator = SkillGenerator(settings)
    return generator.generate(profile, names=names), profile, settings


def test_all_supported_skills_have_templates() -> None:
    assert set(SUPPORTED_SKILLS) == set(_TEMPLATES), (
        "every planned skill must have a renderer; update _TEMPLATES and planner rules together"
    )


def test_generate_fastapi_skills() -> None:
    skills, _profile, _settings = analyze_and_generate("fastapi-app")
    names = {skill.name for skill in skills}
    assert "project-runner" in names
    assert len(skills) >= 5
    for skill in skills:
        assert skill.mode is GenerationMode.DETERMINISTIC
        assert skill.provider is None
        assert skill.bundle.metadata.name == skill.name
        assert skill.bundle.metadata.description
        assert skill.bundle.body.strip()
        assert skill.provenance
        assert skill.candidate is not None


def test_project_runner_contains_evidenced_commands() -> None:
    skills, _profile, _settings = analyze_and_generate("fastapi-app", names=["project-runner"])
    bundle = skills[0].bundle
    commands = [line.strip() for line in bundle.body.splitlines()]
    assert any("uvicorn" in line for line in commands) or any(
        "make run" in line for line in commands
    )
    # The make/uvicorn commands are evidenced by real files.
    assert "Makefile" in bundle.body or "pyproject.toml" in bundle.body


def test_frontmatter_is_valid_and_matches_directory() -> None:
    skills, _profile, _settings = analyze_and_generate("nextjs-app", names=["project-runner"])
    bundle = skills[0].bundle
    parsed = split_frontmatter(bundle.skill_md())
    assert parsed.error is None
    assert parsed.metadata is not None
    assert parsed.metadata["name"] == "project-runner"
    assert len(parsed.metadata["description"]) <= 1024
    metadata = SkillMetadata.model_validate(
        {
            key: value
            for key, value in parsed.metadata.items()
            if key in {"name", "description", "license", "compatibility", "metadata"}
        }
    )
    assert metadata.name == "project-runner"


def test_generated_scripts_are_valid_python() -> None:
    skills, _profile, _settings = analyze_and_generate("fastapi-app")
    found_scripts = False
    for skill in skills:
        for path, content in skill.bundle.all_contents().items():
            if path.startswith("scripts/") and path.endswith(".py"):
                found_scripts = True
                ast.parse(content, filename=path)
    assert found_scripts, "at least one skill must ship a script"


def test_run_steps_script_excludes_dangerous_commands() -> None:
    skills, _profile, _settings = analyze_and_generate("hostile-repo")
    for skill in skills:
        for path, content in skill.bundle.all_contents().items():
            if path.endswith("run_steps.py"):
                assert "rm -rf /" not in content
                assert "| sh" not in content
    # Destructive commands are reported, not embedded.
    assert any("destructive" in warning for skill in skills for warning in skill.warnings)


def test_blueprint_excludes_dangerous_commands_but_reports_them() -> None:
    root = FIXTURES / "hostile-repo"
    settings = load_settings(root, env={})
    profile = analyze_repository(root, settings).profile
    plan = SkillPlanner().plan(profile)
    candidate = plan.candidate("release-verifier") or plan.candidate("test-runner")
    assert candidate is not None
    blueprint = build_blueprint(candidate, profile)
    assert not any(command.command == "rm -rf /" for command in blueprint.commands)
    assert (
        "make deploy" in blueprint.excluded_dangerous or "rm -rf /" in blueprint.excluded_dangerous
    )


def test_generation_is_deterministic() -> None:
    first, profile, settings = analyze_and_generate("fastapi-app")
    second = SkillGenerator(settings).generate(profile)
    for left, right in zip(first, second, strict=True):
        assert left.bundle.all_contents() == right.bundle.all_contents()


@pytest.mark.parametrize(
    ("fixture", "skill"),
    [
        ("fastapi-app", "migration-guardian"),
        ("nextjs-app", "database-debugger"),
        ("go-app", "test-runner"),
        ("dotnet-app", "project-builder"),
        ("flutter-app", "code-reviewer"),
    ],
)
def test_render_every_fixture_skill(fixture: str, skill: str) -> None:
    root = FIXTURES / fixture
    settings = load_settings(root, env={})
    profile = analyze_repository(root, settings).profile
    plan = SkillPlanner().plan(profile)
    candidate = plan.candidate(skill)
    if candidate is None:
        pytest.skip(f"{skill} is not recommended for {fixture}")
    generated = SkillGenerator(settings).generate_candidate(candidate, profile)
    assert generated.bundle.metadata.name == skill
    assert len(generated.bundle.body) > 200
    # Every reference must be referenced or at least well-formed.
    for path in generated.bundle.paths():
        assert path.split("/", 1)[0] in {"references", "scripts", "assets"}


def test_skill_description_includes_repository_name() -> None:
    skills, profile, _settings = analyze_and_generate("go-app", names=["project-runner"])
    assert profile.name in skills[0].bundle.metadata.description


def test_max_command_risk_filters_review_commands() -> None:
    root = FIXTURES / "fastapi-app"
    base = load_settings(root, env={})
    profile = analyze_repository(root, base).profile
    strict = base.model_copy(deep=True)
    strict.security.max_command_risk = "safe"
    generated = SkillGenerator(strict).generate(profile, names=["project-runner"])
    skill = generated[0]
    body_commands = [
        line
        for block in fenced_code_blocks(skill.bundle.body)
        for line in block.content.splitlines()
        if line.strip()
    ]
    assert body_commands
    for command in body_commands:
        assert classify_command(command).is_safe, command


def test_only_safe_flag_is_honoured() -> None:
    root = FIXTURES / "fastapi-app"
    settings = load_settings(root, env={})
    profile = analyze_repository(root, settings).profile
    skill = SkillGenerator(settings, only_safe=True).generate(profile, names=["project-runner"])[0]
    for block in fenced_code_blocks(skill.bundle.body):
        for line in block.content.splitlines():
            if line.strip():
                assert classify_command(line).is_safe, line


def test_render_skill_rejects_unknown_skill() -> None:
    root = FIXTURES / "fastapi-app"
    settings = load_settings(root, env={})
    profile = analyze_repository(root, settings).profile
    plan = SkillPlanner().plan(profile)
    candidate = plan.candidates[0]
    blueprint = build_blueprint(candidate, profile)
    from skillforge.errors import ExampleError

    with pytest.raises(ExampleError):
        render_skill("not-a-real-skill", blueprint)


def test_bundle_paths_are_safe() -> None:
    skills, _profile, _settings = analyze_and_generate("monorepo-mixed")
    for skill in skills:
        for path in skill.bundle.paths():
            assert not path.startswith(("/", ".."))
            assert "\\" not in path
            assert ".." not in path.split("/")


def test_generate_named_subset_rejects_unplanned_skill() -> None:
    root = FIXTURES / "go-app"
    settings = load_settings(root, env={})
    profile = analyze_repository(root, settings).profile
    from skillforge.errors import ExampleError

    with pytest.raises(ExampleError):
        SkillGenerator(settings).generate(profile, names=["migration-guardian"])


def test_scripts_directory_is_referenced_from_body() -> None:
    skills, _profile, _settings = analyze_and_generate("fastapi-app")
    for skill in skills:
        scripts = [path for path in skill.bundle.paths() if path.startswith("scripts/")]
        if not scripts:
            continue
        assert any(path in skill.bundle.body for path in scripts), skill.name


def test_command_blocks_never_contain_dangerous_commands() -> None:
    skills, _profile, _settings = analyze_and_generate("hostile-repo")
    for skill in skills:
        for block in fenced_code_blocks(skill.bundle.body):
            for line in block.content.splitlines():
                if line.strip():
                    assert not classify_command(line).is_dangerous, (skill.name, line)
