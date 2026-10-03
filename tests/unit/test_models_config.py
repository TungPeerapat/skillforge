"""Tests for domain models and the configuration loader."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError
from tests.conftest import REPO_ROOT, write_file

from skillforge.config import Settings, load_settings, user_config_path
from skillforge.errors import ConfigError
from skillforge.models import (
    Certainty,
    Evidence,
    SkillBundle,
    SkillCandidate,
    SkillFile,
    SkillMetadata,
    confidence_from_evidence,
    is_valid_skill_name,
)
from skillforge.utils.markdown import split_frontmatter


def test_evidence_redacts_snippet_and_truncates() -> None:
    evidence = Evidence(
        source=".env.example",
        locator="L3",
        snippet="API_KEY=sk-proj-" + "a" * 40,
    )
    assert "a" * 40 not in evidence.snippet
    long = Evidence(source="README.md", snippet="x" * 1000)
    assert len(long.snippet) < 500


def test_evidence_label() -> None:
    assert (
        Evidence(source="package.json", locator="scripts.dev").label == "package.json:scripts.dev"
    )
    assert Evidence(source="go.mod").label == "go.mod"


def test_confidence_from_evidence() -> None:
    assert confidence_from_evidence([]) == 0.0
    weak = confidence_from_evidence([Evidence(source="a", weight=0.4)])
    strong = confidence_from_evidence([Evidence(source="a", weight=0.9)])
    assert weak < strong < 1.0
    many = confidence_from_evidence([Evidence(source=str(i), weight=0.8) for i in range(5)])
    assert many <= 0.99


@pytest.mark.parametrize(
    ("name", "valid"),
    [
        ("project-runner", True),
        ("a", True),
        ("a1-b2", True),
        ("Project-Runner", False),
        ("project--runner", False),
        ("-runner", False),
        ("runner-", False),
        ("runner_thing", False),
        ("x" * 65, False),
        ("", False),
    ],
)
def test_skill_name_validation(name: str, valid: bool) -> None:
    assert is_valid_skill_name(name) is valid
    if valid:
        assert SkillMetadata(name=name, description="d").name == name
    else:
        with pytest.raises(PydanticValidationError):
            SkillMetadata(name=name, description="d")


def test_metadata_limits_and_frontmatter() -> None:
    metadata = SkillMetadata(
        name="demo",
        description="Use when demonstrating.",
        license="MIT",
        compatibility="Requires git",
        metadata={"author": "team"},
    )
    frontmatter = metadata.to_frontmatter()
    assert frontmatter["name"] == "demo"
    assert "allowed-tools" not in frontmatter
    with pytest.raises(PydanticValidationError):
        SkillMetadata(name="demo", description="x" * 1025)
    with pytest.raises(PydanticValidationError):
        SkillMetadata(name="demo", description="ok", compatibility="x" * 501)


def test_bundle_renders_skill_md_and_roundtrips() -> None:
    bundle = SkillBundle(
        metadata=SkillMetadata(name="project-runner", description="Run the project."),
        body="# Project runner\n\nUse `npm run dev`.",
        files={
            "references/commands.md": SkillFile(
                path="references/commands.md", content="# Commands"
            ),
            "scripts/preflight.py": SkillFile(
                path="scripts/preflight.py", content="print('ok')\n", executable=True
            ),
        },
    )
    text = bundle.skill_md()
    parsed = split_frontmatter(text)
    assert parsed.metadata is not None
    assert parsed.metadata["name"] == "project-runner"
    assert "npm run dev" in parsed.body
    assert bundle.total_tokens() > 0
    assert set(bundle.all_contents()) == {
        "SKILL.md",
        "references/commands.md",
        "scripts/preflight.py",
    }


def test_bundle_rejects_unsafe_paths() -> None:
    with pytest.raises(PydanticValidationError):
        SkillFile(path="../escape.md", content="x")
    with pytest.raises(PydanticValidationError):
        SkillFile(path="C:\\abs.md", content="x")


def test_bundle_rejects_duplicate_paths_case_insensitively() -> None:
    with pytest.raises(PydanticValidationError):
        SkillBundle(
            metadata=SkillMetadata(name="demo", description="d"),
            files={
                "references/a.md": SkillFile(path="references/a.md", content="1"),
                "references/A.md": SkillFile(path="references/A.md", content="2"),
            },
        )


def test_candidate_defaults() -> None:
    candidate = SkillCandidate(name="test-runner", title="Test runner", reason="pytest found")
    assert candidate.certainty is Certainty.INFERENCE
    assert candidate.recommended


# --------------------------------------------------------------- configuration


def test_load_settings_defaults(tmp_path: Path) -> None:
    settings = load_settings(tmp_path, env={})
    assert settings.skills.output == ".skills"
    assert settings.provider.default == "none"
    assert settings.security.allow_external_transmission is False
    assert settings.analysis.max_file_size == 200_000
    assert settings.config_path is None


def test_toml_discovery_and_env_precedence(tmp_path: Path) -> None:
    write_file(
        tmp_path,
        "skillforge.toml",
        '[project]\nname = "from-toml"\n\n[analysis]\nmax_file_size = 1234\n',
    )
    settings = load_settings(tmp_path, env={})
    assert settings.project.name == "from-toml"
    assert settings.analysis.max_file_size == 1234
    assert settings.config_path is not None

    overridden = load_settings(
        tmp_path,
        env={"SKILLFORGE_ANALYSIS__MAX_FILE_SIZE": "9999", "SKILLFORGE_PROJECT__NAME": "env-name"},
    )
    assert overridden.analysis.max_file_size == 9999
    assert overridden.project.name == "env-name"

    cli = load_settings(tmp_path, env={}, overrides={"analysis": {"max_file_size": 5555}})
    assert cli.analysis.max_file_size == 5555


def test_config_searched_in_parent(tmp_path: Path) -> None:
    write_file(tmp_path, "skillforge.toml", '[project]\nname = "parent"\n')
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    settings = load_settings(nested, env={})
    assert settings.project.name == "parent"


def test_user_config_merging(tmp_path: Path) -> None:
    user_config = tmp_path / "user.toml"
    user_config.write_text('[provider]\ndefault = "mock"\n', encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    settings = load_settings(repo, env={"SKILLFORGE_USER_CONFIG": str(user_config)})
    assert settings.provider.default == "mock"
    assert settings.user_config_path == str(user_config)
    # Project config wins over user config.
    write_file(repo, "skillforge.toml", '[provider]\ndefault = "openai"\n')
    project = load_settings(repo, env={"SKILLFORGE_USER_CONFIG": str(user_config)})
    assert project.provider.default == "openai"


def test_explicit_config_must_exist(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_settings(tmp_path, config_file=tmp_path / "missing.toml", env={})


def test_invalid_toml_reports_error(tmp_path: Path) -> None:
    write_file(tmp_path, "skillforge.toml", "not valid = = toml")
    with pytest.raises(ConfigError) as excinfo:
        load_settings(tmp_path, env={})
    assert "skillforge.toml" in str(excinfo.value)


def test_unsafe_output_path_rejected(tmp_path: Path) -> None:
    write_file(tmp_path, "skillforge.toml", '[skills]\noutput = "../outside"\n')
    with pytest.raises(ConfigError):
        load_settings(tmp_path, env={})


def test_command_execution_rejected_in_this_release(tmp_path: Path) -> None:
    write_file(
        tmp_path,
        "skillforge.toml",
        "[security]\nallow_command_execution = true\n",
    )
    with pytest.raises(ConfigError) as excinfo:
        load_settings(tmp_path, env={})
    assert "allow_command_execution" in str(excinfo.value)


def test_provider_api_key_env_resolution() -> None:
    settings = Settings.model_validate({"provider": {"default": "openai"}})
    assert settings.provider_api_key_env() == "OPENAI_API_KEY"
    explicit = Settings.model_validate({"provider": {"default": "openai", "api_key_env": "MY_KEY"}})
    assert explicit.provider_api_key_env() == "MY_KEY"
    assert Settings().provider.enabled is False


def test_version_is_single_sourced() -> None:
    """The runtime version must come from pyproject.toml, not a second constant."""
    import tomllib

    import skillforge

    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    distribution_version = pyproject["project"]["version"]
    if skillforge.__version__ == "0.0.0.dev0":
        return  # raw source checkout that was never installed
    assert skillforge.__version__ == distribution_version


def test_user_config_path_uses_env_override() -> None:
    path = user_config_path({"SKILLFORGE_USER_CONFIG": "~/custom.toml"})
    assert str(path).endswith("custom.toml")
