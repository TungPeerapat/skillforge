"""End-to-end tests: the workflow a user actually runs."""

from __future__ import annotations

import json
from pathlib import Path

from tests.conftest import REPO_ROOT
from typer.testing import CliRunner

from skillforge.analyzer import analyze_repository
from skillforge.cli.main import app
from skillforge.config import load_settings
from skillforge.exporters import exporter_ids, get_exporter
from skillforge.generator import SkillGenerator
from skillforge.planner import SkillPlanner
from skillforge.skills import read_bundle, write_generated_skill
from skillforge.utils.markdown import split_frontmatter
from skillforge.validator import SkillValidator

runner = CliRunner()


def test_full_pipeline_on_a_copied_fixture(copy_fixture, tmp_path: Path) -> None:
    repo = copy_fixture("fastapi-app")
    settings = load_settings(repo, env={})

    analysis = analyze_repository(repo, settings)
    profile = analysis.profile
    plan = SkillPlanner().plan(profile)
    assert plan.names

    skills = SkillGenerator(settings).generate(profile, plan=plan)
    output_dir = settings.output_dir(repo)
    for skill in skills:
        write_generated_skill(skill, output_dir, repository=profile.name)

    # Every skill reads back cleanly and validates.
    validator = SkillValidator(profile=profile)
    results = validator.validate_output_dir(output_dir)
    assert len(results) == len(skills)
    assert all(result.ok for result in results)

    # Every exporter accepts every skill.
    for exporter_id in exporter_ids():
        exporter = get_exporter(exporter_id)
        exported = exporter.export_directory(output_dir, repo, force=True)
        assert exported == sorted(skill.name for skill in skills)
        for name in exported:
            stored = read_bundle(exporter.skill_dir(repo, name))
            assert stored.bundle.metadata.name == name
            assert split_frontmatter(stored.bundle.skill_md()).error is None


def test_cli_analyze_json(tmp_path: Path, copy_fixture) -> None:
    repo = copy_fixture("go-app")
    result = runner.invoke(app, ["analyze", str(repo), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["repository"]["name"] == "go-app"
    assert "profile" in payload
    assert payload["plan"]["candidates"]


def test_cli_generate_validate_export_clean(tmp_path: Path, copy_fixture) -> None:
    repo = copy_fixture("nextjs-app")

    generated = runner.invoke(app, ["generate", str(repo)])
    assert generated.exit_code == 0, generated.output
    output_dir = repo / ".skills"
    assert (output_dir / "project-runner" / "SKILL.md").is_file()

    validated = runner.invoke(app, ["validate", str(repo)])
    assert validated.exit_code == 0, validated.output

    listed = runner.invoke(app, ["list", str(repo), "--generated", "--json"])
    assert listed.exit_code == 0, listed.output
    names = [entry["name"] for entry in json.loads(listed.output)["skills"]]
    assert "project-runner" in names

    exported = runner.invoke(app, ["export", str(repo), "--to", "claude,codex"])
    assert exported.exit_code == 0, exported.output
    assert (repo / ".claude" / "skills" / "project-runner" / "SKILL.md").is_file()
    assert (repo / ".agents" / "skills" / "project-runner" / "SKILL.md").is_file()

    dry = runner.invoke(app, ["clean", str(repo)])
    assert dry.exit_code == 0
    assert (output_dir / "project-runner").is_dir()  # dry run keeps files

    cleaned = runner.invoke(app, ["clean", str(repo), "--yes"])
    assert cleaned.exit_code == 0, cleaned.output
    assert not (output_dir / "project-runner").exists()


def test_cli_generate_single_skill_and_force(tmp_path: Path, copy_fixture) -> None:
    repo = copy_fixture("go-app")
    first = runner.invoke(app, ["generate", str(repo), "test-runner"])
    assert first.exit_code == 0, first.output
    assert (repo / ".skills" / "test-runner").is_dir()
    assert not (repo / ".skills" / "project-runner").exists()

    refused = runner.invoke(app, ["generate", str(repo), "test-runner"])
    assert refused.exit_code == 2
    forced = runner.invoke(app, ["generate", str(repo), "test-runner", "--force"])
    assert forced.exit_code == 0, forced.output


def test_cli_generate_unknown_skill_fails_cleanly(tmp_path: Path, copy_fixture) -> None:
    repo = copy_fixture("go-app")
    result = runner.invoke(app, ["generate", str(repo), "migration-guardian"])
    assert result.exit_code == 1
    assert "not recommended" in result.output


def test_cli_validate_without_skills_fails_with_hint(tmp_path: Path) -> None:
    repo = tmp_path / "empty"
    repo.mkdir()
    result = runner.invoke(app, ["validate", str(repo)])
    assert result.exit_code == 1
    assert "generate" in result.output


def test_cli_init_writes_config_and_gitignore(tmp_path: Path) -> None:
    repo = tmp_path / "fresh"
    repo.mkdir()
    result = runner.invoke(app, ["init", str(repo)])
    assert result.exit_code == 0, result.output
    assert (repo / "skillforge.toml").is_file()
    gitignore = (repo / ".gitignore").read_text(encoding="utf-8")
    assert ".skills/" in gitignore

    again = runner.invoke(app, ["init", str(repo)])
    assert again.exit_code == 2  # refuses without --force
    forced = runner.invoke(app, ["init", str(repo), "--force"])
    assert forced.exit_code == 0


def test_cli_doctor_json(tmp_path: Path) -> None:
    result = runner.invoke(app, ["doctor", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    names = [check["name"] for check in payload["checks"]]
    assert "Python" in names
    assert "Git" in names
    assert not any("sk-" in json.dumps(check) for check in payload["checks"])


def test_cli_analyze_reports_missing_path() -> None:
    result = runner.invoke(app, ["analyze", "definitely/not/here"])
    assert result.exit_code == 1
    assert "does not exist" in result.output or "Not a directory" in result.output


def test_cli_eval_json() -> None:
    result = runner.invoke(
        app,
        [
            "eval",
            "--repo",
            str(REPO_ROOT),
            "--scenario",
            "go-add-handler",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["scenarios"][0]["scenario_id"] == "go-add-handler"
    assert payload["comparisons"]


def test_json_output_is_parseable_when_logging(tmp_path: Path, copy_fixture) -> None:
    """--json must keep stdout machine-readable even with -v logging."""
    repo = copy_fixture("go-app")
    result = runner.invoke(app, ["--verbose", "analyze", str(repo), "--json"])
    assert result.exit_code == 0, result.output
    json.loads(result.output)


def test_cli_export_uses_configured_targets(tmp_path: Path, copy_fixture) -> None:
    repo = copy_fixture("go-app")
    (repo / "skillforge.toml").write_text('[export]\ntargets = ["codex"]\n', encoding="utf-8")
    generated = runner.invoke(app, ["generate", str(repo)])
    assert generated.exit_code == 0, generated.output

    exported = runner.invoke(app, ["export", str(repo)])
    assert exported.exit_code == 0, exported.output
    assert (repo / ".agents" / "skills" / "project-runner" / "SKILL.md").is_file()
    assert not (repo / ".claude").exists()


def test_cli_hostile_repo_never_embeds_dangerous_commands(tmp_path: Path, copy_fixture) -> None:
    repo = copy_fixture("hostile-repo")
    result = runner.invoke(app, ["generate", str(repo)])
    assert result.exit_code == 0, result.output
    for skill_dir in (repo / ".skills").iterdir():
        content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        assert "rm -rf /" not in content
        assert "| sh" not in content
