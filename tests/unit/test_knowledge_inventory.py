"""Unit tests for the deep dependency inventory (K1)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from skillforge.analyzer import analyze_repository
from skillforge.config import load_settings
from skillforge.errors import ConfigError
from skillforge.knowledge import build_inventory, classify_role, detect_pin
from skillforge.models import (
    Certainty,
    Command,
    Dependency,
    DetectedTechnology,
    Evidence,
    RepositoryProfile,
)
from skillforge.models.knowledge import (
    DependencyRelationship,
    DependencyRole,
    KnowledgePriority,
    ResolutionMethod,
)
from skillforge.models.repository import DependencyScope, TechnologyKind


def _analyzed(fixture: str, copy_fixture, tmp_path: Path):
    repo = copy_fixture(fixture)
    settings = load_settings(repo, env={})
    analysis = analyze_repository(repo, settings)
    return analysis.profile, settings


def _dependency(
    name: str,
    spec: str | None,
    *,
    ecosystem: str = "pypi",
    scope: DependencyScope = DependencyScope.RUNTIME,
) -> Dependency:
    return Dependency(
        ecosystem=ecosystem,
        name=name,
        version_spec=spec,
        scope=scope,
        manifest="pyproject.toml",
        evidence=[Evidence(source="pyproject.toml", locator=f"dependency {name}", weight=0.7)],
    )


def _profile(
    *,
    dependencies: list[Dependency] | None = None,
    technologies: list[DetectedTechnology] | None = None,
) -> RepositoryProfile:
    return RepositoryProfile(
        generated_at=datetime.now(UTC),
        name="demo",
        root_path=".",
        dependencies=dependencies or [],
        technologies=technologies or [],
    )


# ------------------------------------------------------------------ fixtures


def test_inventory_classifies_declared_dependencies(copy_fixture, tmp_path: Path) -> None:
    profile, settings = _analyzed("fastapi-app", copy_fixture, tmp_path)
    inventory = build_inventory(profile, settings)
    assert inventory.entries

    fastapi = inventory.by_name("fastapi")
    assert fastapi is not None
    assert fastapi.role is DependencyRole.FRAMEWORK
    assert fastapi.relationship is DependencyRelationship.DIRECT
    assert fastapi.scope is DependencyScope.RUNTIME
    assert fastapi.importance is KnowledgePriority.CRITICAL
    assert fastapi.version.method is ResolutionMethod.MANIFEST_RANGE
    assert fastapi.version.resolved is None

    sqlalchemy = inventory.by_name("sqlalchemy")
    assert sqlalchemy is not None
    assert sqlalchemy.role is DependencyRole.ORM
    assert sqlalchemy.importance is KnowledgePriority.HIGH

    alembic = inventory.by_name("alembic")
    assert alembic is not None
    assert alembic.role is DependencyRole.MIGRATION_TOOL

    pytest_dep = inventory.by_name("pytest")
    assert pytest_dep is not None
    assert pytest_dep.role is DependencyRole.TEST_TOOL
    assert pytest_dep.scope is DependencyScope.DEVELOPMENT
    assert pytest_dep.importance is KnowledgePriority.MEDIUM


def test_inventory_resolves_exact_npm_pins(copy_fixture, tmp_path: Path) -> None:
    profile, settings = _analyzed("nextjs-app", copy_fixture, tmp_path)
    inventory = build_inventory(profile, settings)

    next_dep = inventory.by_name("next")
    assert next_dep is not None
    assert next_dep.role is DependencyRole.FRAMEWORK
    assert next_dep.version.resolved == "16.0.1"
    assert next_dep.version.method is ResolutionMethod.MANIFEST_PIN
    assert next_dep.version.certainty is Certainty.FACT

    prisma_client = inventory.by_name("@prisma/client")
    assert prisma_client is not None
    assert prisma_client.role is DependencyRole.ORM

    prisma_cli = inventory.by_name("prisma")
    assert prisma_cli is not None
    assert prisma_cli.role is DependencyRole.MIGRATION_TOOL

    targets = {target.technology: target for target in inventory.knowledge_targets}
    assert targets["next"].version == "16.0.1"
    assert targets["next"].ecosystem == "npm"
    assert "react" in targets


def test_inventory_is_deterministic(copy_fixture, tmp_path: Path) -> None:
    profile, settings = _analyzed("fastapi-app", copy_fixture, tmp_path)
    first = build_inventory(profile, settings)
    second = build_inventory(profile, settings)
    assert first.model_dump(exclude={"generated_at"}) == second.model_dump(exclude={"generated_at"})


def test_priority_override_wins(copy_fixture, tmp_path: Path) -> None:
    repo = copy_fixture("fastapi-app")
    settings = load_settings(
        repo, env={}, overrides={"knowledge": {"priority": {"fastapi": "low"}}}
    )
    profile = analyze_repository(repo, settings).profile
    inventory = build_inventory(profile, settings)
    fastapi = inventory.by_name("fastapi")
    assert fastapi is not None
    assert fastapi.importance is KnowledgePriority.LOW


def test_inventory_cap_keeps_most_important() -> None:
    dependencies = [
        _dependency("unknown-a", "1.0.0"),
        _dependency("unknown-b", "1.0.0"),
        _dependency("fastapi", ">=0.1"),
        _dependency("requests", "2.0.0"),
        _dependency("flask", ">=3"),
        _dependency("httpx", ">=0.27"),
    ]
    settings = load_settings(
        Path.cwd(), env={}, overrides={"knowledge": {"max_inventory_entries": 3}}
    )
    inventory = build_inventory(_profile(dependencies=dependencies), settings)
    assert inventory.truncated is True
    assert len(inventory.entries) == 3
    assert inventory.entries[0].name.lower() in {"fastapi", "flask"}
    assert any("truncated" in warning for warning in inventory.warnings)


def test_runtime_technology_becomes_a_runtime_entry() -> None:
    profile = _profile(
        technologies=[
            DetectedTechnology(
                kind=TechnologyKind.RUNTIME,
                name="Python",
                version="3.12",
                evidence=[Evidence(source="pyproject.toml", weight=0.8)],
            )
        ]
    )
    inventory = build_inventory(profile, load_settings(Path.cwd(), env={}))
    entry = inventory.by_name("Python")
    assert entry is not None
    assert entry.ecosystem == "runtime"
    assert entry.role is DependencyRole.RUNTIME
    assert entry.version.declared == "3.12"
    # Runtime versions are not package knowledge targets.
    assert all(target.technology != "Python" for target in inventory.knowledge_targets)


def test_command_tooling_is_inventoried_without_a_declaration() -> None:
    profile = _profile()
    profile.commands = [
        Command(
            command="docker compose up -d postgres",
            evidence=[Evidence(source="README.md", locator="L10", weight=0.6)],
        )
    ]
    inventory = build_inventory(profile, load_settings(Path.cwd(), env={}))
    docker = inventory.by_name("Docker")
    assert docker is not None
    assert docker.role is DependencyRole.CONTAINER_TOOL
    assert docker.relationship is DependencyRelationship.DIRECT
    assert "used by repository commands" in docker.notes


def test_declared_dependency_wins_over_command_tooling() -> None:
    profile = _profile(
        dependencies=[_dependency("pytest", ">=8", scope=DependencyScope.DEVELOPMENT)]
    )
    profile.commands = [
        Command(command="pytest -q", evidence=[Evidence(source="Makefile", locator="L4")])
    ]
    inventory = build_inventory(profile, load_settings(Path.cwd(), env={}))
    assert len([entry for entry in inventory.entries if entry.name.lower() == "pytest"]) == 1
    entry = inventory.by_name("pytest")
    assert entry is not None
    assert entry.ecosystem == "pypi"


# -------------------------------------------------------------- pin detection


@pytest.mark.parametrize(
    ("spec", "ecosystem", "expected"),
    [
        ("==1.2.3", "pypi", ("1.2.3", ResolutionMethod.MANIFEST_PIN)),
        ("===1.2.3", "pypi", ("1.2.3", ResolutionMethod.MANIFEST_PIN)),
        ("==1.2.3; python_version >= '3.12'", "pypi", ("1.2.3", ResolutionMethod.MANIFEST_PIN)),
        (">=1.2", "pypi", (None, ResolutionMethod.MANIFEST_RANGE)),
        ("~=1.2", "pypi", (None, ResolutionMethod.MANIFEST_RANGE)),
        ("16.0.1", "npm", ("16.0.1", ResolutionMethod.MANIFEST_PIN)),
        ("^16.0.1", "npm", (None, ResolutionMethod.MANIFEST_RANGE)),
        ("~16.0.1", "npm", (None, ResolutionMethod.MANIFEST_RANGE)),
        ("16", "npm", (None, ResolutionMethod.MANIFEST_RANGE)),
        ("workspace:*", "npm", (None, ResolutionMethod.MANIFEST_RANGE)),
        ("v1.2.3", "go", ("v1.2.3", ResolutionMethod.MANIFEST_PIN)),
        ("[1.2.3]", "nuget", ("1.2.3", ResolutionMethod.MANIFEST_PIN)),
        ("1.2.3", "nuget", (None, ResolutionMethod.MANIFEST_RANGE)),
        ("1.2.3", "maven", ("1.2.3", ResolutionMethod.MANIFEST_PIN)),
        ("^1.2.3", "pub", (None, ResolutionMethod.MANIFEST_RANGE)),
        ("1.2.3", "pub", ("1.2.3", ResolutionMethod.MANIFEST_PIN)),
        (None, "pypi", (None, ResolutionMethod.NONE)),
        ("", "pypi", (None, ResolutionMethod.NONE)),
    ],
)
def test_detect_pin(spec, ecosystem, expected) -> None:
    assert detect_pin(spec, ecosystem) == expected


def test_classify_role_prefers_exact_overrides() -> None:
    assert classify_role("prisma", []) is DependencyRole.MIGRATION_TOOL
    assert classify_role("@prisma/client", []) is DependencyRole.ORM
    assert classify_role("@tanstack/react-query", []) is DependencyRole.LIBRARY
    assert classify_role("react", []) is DependencyRole.FRAMEWORK
    assert classify_role("react-dom", []) is DependencyRole.FRAMEWORK
    assert classify_role("sentry-sdk", []) is DependencyRole.OBSERVABILITY
    assert classify_role("boto3", []) is DependencyRole.SDK
    assert classify_role("@angular/core", []) is DependencyRole.FRAMEWORK
    assert classify_role("unknown-package", []) is DependencyRole.LIBRARY
    # Word boundaries: "ent" must not match "contentful", "helm" not "helmet".
    assert classify_role("contentful", []) is DependencyRole.LIBRARY
    assert classify_role("helmet", []) is DependencyRole.LIBRARY


# ------------------------------------------------------------------- config


def test_knowledge_settings_reject_invalid_priority(tmp_path: Path) -> None:
    (tmp_path / "skillforge.toml").write_text(
        '[knowledge]\npriority = { fastapi = "urgent" }\n', encoding="utf-8"
    )
    with pytest.raises(ConfigError):
        load_settings(tmp_path, env={})


def test_knowledge_settings_defaults(tmp_path: Path) -> None:
    settings = load_settings(tmp_path, env={})
    assert settings.knowledge.max_inventory_entries == 2000
    assert settings.knowledge.priority == {}
    assert settings.knowledge.read_lockfiles is False
    assert settings.knowledge.max_lockfile_bytes == 32 * 1024 * 1024
    loaded = load_settings(
        tmp_path,
        env={},
        overrides={"knowledge": {"priority": {"FastAPI": "High"}}},
    )
    assert loaded.knowledge.priority == {"FastAPI": "high"}


def test_knowledge_lockfile_settings_round_trip(tmp_path: Path) -> None:
    loaded = load_settings(
        tmp_path,
        env={},
        overrides={"knowledge": {"read_lockfiles": True, "max_lockfile_bytes": 4096}},
    )
    assert loaded.knowledge.read_lockfiles is True
    assert loaded.knowledge.max_lockfile_bytes == 4096
