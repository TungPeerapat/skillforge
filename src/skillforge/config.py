"""Configuration loading.

Precedence (lowest to highest):

1. built-in defaults
2. user configuration (``$SKILLFORGE_USER_CONFIG``, else the platform config dir)
3. project ``skillforge.toml`` (explicit ``--config`` or discovered upward)
4. ``SKILLFORGE_*`` environment variables
5. CLI overrides

Environment variable grammar: ``SKILLFORGE_<SECTION>__<FIELD>`` using a double
underscore between the section and the field, for example::

    SKILLFORGE_ANALYSIS__MAX_FILE_SIZE=100000
    SKILLFORGE_SECURITY__ALLOW_EXTERNAL_TRANSMISSION=true
    SKILLFORGE_PROVIDER__DEFAULT=openai

Values are parsed as JSON when possible and otherwise treated as strings.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from skillforge.errors import ConfigError
from skillforge.security.paths import PathViolation, assert_safe_relative, safe_join
from skillforge.utils.toml import load_toml_file

CONFIG_FILENAME = "skillforge.toml"
ENV_PREFIX = "SKILLFORGE_"
USER_CONFIG_ENV = "SKILLFORGE_USER_CONFIG"

_SECTIONS = frozenset(
    {"project", "analysis", "provider", "skills", "security", "export", "evaluation"}
)
_MAX_PARENT_SEARCH = 5


class ProjectSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = ""


class AnalysisSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_file_size: int = Field(default=200_000, ge=1_000, le=50_000_000)
    max_context_tokens: int = Field(default=50_000, ge=1_000, le=2_000_000)
    max_files_per_category: int = Field(default=40, ge=1, le=1_000)
    follow_symlinks: bool = False
    ignore: list[str] = Field(default_factory=list)
    include: list[str] = Field(default_factory=list)


class ProviderSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default: str = "none"
    model: str = ""
    base_url: str = ""
    api_key_env: str = ""
    max_output_tokens: int = Field(default=4_096, ge=1, le=200_000)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    timeout_seconds: float = Field(default=60.0, gt=0.0, le=600.0)

    @property
    def enabled(self) -> bool:
        return self.default.strip().lower() not in ("", "none")


class SkillsSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output: str = ".skills"
    author: str = ""
    license: str = ""
    overwrite: bool = False

    @field_validator("output")
    @classmethod
    def _validate_output(cls, value: str) -> str:
        try:
            cleaned = assert_safe_relative(value)
        except PathViolation as exc:
            raise ValueError(f"skills.output must be a safe relative path: {exc}") from exc
        if cleaned == ".":
            raise ValueError("skills.output must not be the repository root")
        return cleaned


class SecuritySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow_command_execution: bool = False
    allow_external_transmission: bool = False
    max_command_risk: Literal["safe", "review"] = "review"
    redact_secrets: bool = True

    @model_validator(mode="after")
    def _no_execution_in_v01(self) -> SecuritySettings:
        if self.allow_command_execution:
            raise ValueError(
                "command execution is not implemented in this release; "
                "leave security.allow_command_execution = false"
            )
        return self


class ExportSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    targets: list[str] = Field(default_factory=list)


class EvaluationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeout_seconds: int = Field(default=300, ge=1, le=3_600)


class Settings(BaseModel):
    """Validated SkillForge configuration."""

    model_config = ConfigDict(extra="forbid")

    project: ProjectSettings = Field(default_factory=ProjectSettings)
    analysis: AnalysisSettings = Field(default_factory=AnalysisSettings)
    provider: ProviderSettings = Field(default_factory=ProviderSettings)
    skills: SkillsSettings = Field(default_factory=SkillsSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    export: ExportSettings = Field(default_factory=ExportSettings)
    evaluation: EvaluationSettings = Field(default_factory=EvaluationSettings)

    #: Path of the project configuration actually used, or ``None``.
    config_path: str | None = None
    #: Path of the user-level configuration, if one was found.
    user_config_path: str | None = None

    # ------------------------------------------------------------------ helpers
    def project_name(self, repo_root: Path) -> str:
        return self.project.name.strip() or repo_root.resolve().name

    def output_dir(self, repo_root: Path) -> Path:
        """Absolute path of the skill output directory (validated)."""
        return safe_join(repo_root, self.skills.output)

    def provider_api_key_env(self) -> str:
        """Environment variable holding the provider key, if known."""
        if self.provider.api_key_env:
            return self.provider.api_key_env
        provider = self.provider.default.strip().lower()
        return {
            "openai": "OPENAI_API_KEY",
            "openai-compatible": "OPENAI_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
        }.get(provider, "")

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


# --------------------------------------------------------------------- loading


def user_config_path(env: Mapping[str, str] | None = None) -> Path:
    """Platform-appropriate user configuration path."""
    environment = env if env is not None else os.environ
    override = environment.get(USER_CONFIG_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = environment.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "skillforge" / "config.toml"
    xdg = environment.get("XDG_CONFIG_HOME", "").strip()
    base_path = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base_path / "skillforge" / "config.toml"


def find_project_config(repo_root: Path) -> Path | None:
    """Search ``repo_root`` and up to five parents for ``skillforge.toml``."""
    current = repo_root.resolve()
    for _ in range(_MAX_PARENT_SEARCH):
        candidate = current / CONFIG_FILENAME
        if candidate.is_file():
            return candidate
        if current == current.parent:
            break
        current = current.parent
    return None


def _deep_merge(base: dict[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, Mapping):
            merged[key] = _deep_merge(existing, value)
        else:
            merged[key] = value
    return merged


def _parse_env_value(raw: str) -> Any:
    stripped = raw.strip()
    if not stripped:
        return ""
    try:
        return json.loads(stripped)
    except ValueError:
        return raw


def _env_overrides(env: Mapping[str, str]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for key, raw in env.items():
        if not key.startswith(ENV_PREFIX):
            continue
        remainder = key[len(ENV_PREFIX) :].lower()
        if "__" not in remainder:
            continue
        section, _, field_name = remainder.partition("__")
        if section not in _SECTIONS or not field_name:
            continue
        data.setdefault(section, {})[field_name] = _parse_env_value(raw)
    return data


def load_settings(
    repo_root: Path,
    *,
    config_file: Path | None = None,
    overrides: Mapping[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
) -> Settings:
    """Load and validate configuration for a repository."""
    environment = dict(env) if env is not None else dict(os.environ)
    data: dict[str, Any] = {}
    user_path = user_config_path(environment)
    if user_path.is_file():
        data = _deep_merge(data, load_toml_file(user_path))

    if config_file is not None:
        explicit = config_file.expanduser()
        if not explicit.is_file():
            raise ConfigError(f"Configuration file not found: {explicit}")
        project_path: Path | None = explicit.resolve()
    else:
        project_path = find_project_config(repo_root)
    if project_path is not None:
        data = _deep_merge(data, load_toml_file(project_path))

    data = _deep_merge(data, _env_overrides(environment))
    if overrides:
        data = _deep_merge(data, overrides)

    try:
        settings = Settings.model_validate(data)
    except ValidationError as exc:
        source = str(project_path) if project_path else "configuration"
        raise ConfigError(f"Invalid {source}:\n{_format_validation_error(exc)}") from exc
    updates: dict[str, Any] = {}
    if project_path is not None:
        updates["config_path"] = str(project_path)
    if user_path.is_file():
        updates["user_config_path"] = str(user_path)
    return settings.model_copy(update=updates) if updates else settings


def _format_validation_error(exc: ValidationError) -> str:
    lines: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "<root>"
        lines.append(f"  - {location}: {error['msg']}")
    return "\n".join(lines)
