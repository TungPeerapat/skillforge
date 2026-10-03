"""Tests for the skill planner."""

from __future__ import annotations

from datetime import UTC, datetime

from tests.conftest import REPO_ROOT

from skillforge.analyzer import analyze_repository
from skillforge.config import load_settings
from skillforge.models import RepositoryProfile
from skillforge.models.skill import SkillPlan
from skillforge.models.workflow import CommandPurpose, WorkflowCategory
from skillforge.planner import SkillPlanner
from skillforge.planner.context import PlanContext
from skillforge.planner.rules import DEFAULT_RULES

FIXTURES = REPO_ROOT / "tests" / "fixtures"


def plan_for(fixture: str) -> SkillPlan:
    root = FIXTURES / fixture
    profile = analyze_repository(root, load_settings(root, env={})).profile
    return SkillPlanner().plan(profile)


def empty_profile() -> RepositoryProfile:
    return RepositoryProfile(
        name="empty",
        root_path=".",
        generated_at=datetime.now(UTC),
    )


def test_fastapi_plan_recommends_expected_skills() -> None:
    plan = plan_for("fastapi-app")
    names = set(plan.names)
    assert {
        "project-runner",
        "test-runner",
        "project-builder",
        "project-debugger",
        "migration-guardian",
        "database-debugger",
        "api-contract-checker",
        "code-reviewer",
    } <= names
    assert "release-verifier" not in names  # no release evidence in this fixture


def test_go_plan_has_no_database_skills() -> None:
    plan = plan_for("go-app")
    names = set(plan.names)
    assert {"project-runner", "test-runner", "project-builder", "api-contract-checker"} <= names
    assert "database-debugger" not in names
    assert "migration-guardian" not in names


def test_flutter_plan_excludes_server_database_skills() -> None:
    plan = plan_for("flutter-app")
    names = set(plan.names)
    assert {"project-runner", "test-runner", "project-builder", "code-reviewer"} <= names
    assert "database-debugger" not in names  # on-device SQLite is not a server dependency


def test_nextjs_plan_detects_prisma_migrations() -> None:
    plan = plan_for("nextjs-app")
    names = set(plan.names)
    assert {"project-runner", "test-runner", "project-builder", "migration-guardian"} <= names
    migration = plan.candidate("migration-guardian")
    assert migration is not None
    assert "Prisma Migrate" in migration.reason


def test_monorepo_plan_is_repo_wide() -> None:
    plan = plan_for("monorepo-mixed")
    names = set(plan.names)
    assert {"project-runner", "test-runner", "database-debugger", "api-contract-checker"} <= names


def test_hostile_plan_does_not_invent_runner() -> None:
    plan = plan_for("hostile-repo")
    names = set(plan.names)
    assert "project-runner" not in names  # there is no run command in the fixture
    assert "release-verifier" in names  # `make deploy` exists (dangerous, but evidenced)


def test_every_candidate_has_reason_and_evidence() -> None:
    for fixture in (
        "fastapi-app",
        "nextjs-app",
        "go-app",
        "dotnet-app",
        "flutter-app",
        "monorepo-mixed",
    ):
        plan = plan_for(fixture)
        assert plan.candidates, fixture
        for candidate in plan.candidates:
            assert candidate.reason, candidate.name
            assert candidate.evidence, candidate.name
            assert 0.0 <= candidate.confidence <= 1.0
            assert candidate.priority >= 0


def test_empty_repository_gets_no_recommendations_and_an_explanation() -> None:
    plan = SkillPlanner().plan(empty_profile())
    assert plan.candidates == []
    assert plan.notes
    assert "No skills were recommended" in plan.notes[0]


def test_plan_is_deterministic() -> None:
    first = plan_for("fastapi-app")
    second = plan_for("fastapi-app")
    drop = {"generated_at"}
    assert first.model_dump(exclude=drop) == second.model_dump(exclude=drop)


def test_missing_dependency_is_annotated() -> None:
    """An API without a run command yields api-contract-checker with a note."""
    profile = empty_profile()
    profile = profile.model_copy(deep=True)
    from skillforge.models import API, Certainty, Evidence
    from skillforge.models.workflow import APIKind

    profile.apis = [
        API(
            kind=APIKind.REST,
            framework="FastAPI",
            evidence=[Evidence(kind=Certainty.FACT, source="pyproject.toml", weight=0.8)],
        )
    ]
    plan = SkillPlanner().plan(profile)
    candidate = plan.candidate("api-contract-checker")
    assert candidate is not None
    assert any("project-runner" in note for note in candidate.notes)
    assert any("project-runner" in note for note in plan.notes)


def test_rule_ids_are_unique() -> None:
    ids = [rule.id for rule in DEFAULT_RULES]
    assert len(ids) == len(set(ids))
    skills = [rule.skill for rule in DEFAULT_RULES]
    assert len(skills) == len(set(skills))


def test_plan_context_helpers() -> None:
    root = FIXTURES / "fastapi-app"
    profile = analyze_repository(root, load_settings(root, env={})).profile
    context = PlanContext(profile=profile)
    assert context.primary_language() == "python"
    assert context.has_technology("FastAPI")
    assert context.databases_with_migrations()
    assert context.technology_version("FastAPI")
    assert context.workflow(WorkflowCategory.TEST) is not None
    assert context.commands(CommandPurpose.RUN)
    assert context.risks == [] or isinstance(context.risks[0], str)
