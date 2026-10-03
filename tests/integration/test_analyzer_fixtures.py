"""Integration tests: analyze every fixture and assert evidence-backed findings.

These tests run the real scanner and detectors over the checked-in fixtures —
no mocks.
"""

from __future__ import annotations

import pytest
from tests.conftest import REPO_ROOT

from skillforge.analyzer import analyze_repository
from skillforge.config import load_settings
from skillforge.models import Certainty
from skillforge.models.workflow import CommandPurpose, WorkflowCategory

FIXTURES = REPO_ROOT / "tests" / "fixtures"


def analyze(fixture: str):
    root = FIXTURES / fixture
    settings = load_settings(root, env={})
    return analyze_repository(root, settings)


def command_texts(profile) -> list[str]:
    return [command.command for command in profile.commands]


# --------------------------------------------------------------------- fastapi


def test_fastapi_stack_detection() -> None:
    profile = analyze("fastapi-app").profile
    assert profile.has_technology("Python")
    assert profile.has_technology("FastAPI")
    assert profile.has_technology("uv")
    assert profile.has_technology("Alembic")
    assert profile.has_technology("Docker Compose")
    assert profile.has_technology("GitHub Actions")
    assert profile.find_dependency("fastapi") is not None
    assert profile.primary_languages()[0] == "python"


def test_fastapi_workflows_and_commands() -> None:
    profile = analyze("fastapi-app").profile
    assert {"setup", "run", "test", "lint", "migrate", "verify"} <= {
        workflow.id for workflow in profile.workflows
    }
    commands = command_texts(profile)
    assert "uvicorn app.main:app" in commands
    assert "pytest" in commands
    assert "alembic upgrade head" in commands
    assert "docker compose up" in commands


def test_fastapi_services_and_databases() -> None:
    profile = analyze("fastapi-app").profile
    service_names = {service.name for service in profile.services}
    assert {"db", "cache"} <= service_names
    engines = {database.engine.value for database in profile.databases}
    assert "postgresql" in engines
    assert "redis" in engines
    assert any(database.migration_tool == "Alembic" for database in profile.databases)
    assert profile.apis and profile.apis[0].framework == "FastAPI"


def test_every_command_has_evidence() -> None:
    profile = analyze("fastapi-app").profile
    for command in profile.commands:
        assert command.evidence, f"command without evidence: {command.command}"
        assert command.evidence[0].source


def test_environment_keys_are_collected_without_values() -> None:
    profile = analyze("fastapi-app").profile
    assert "DATABASE_URL" in profile.environment_keys
    assert "REDIS_URL" in profile.environment_keys
    assert profile.environment_files == [".env.example"]


# ---------------------------------------------------------------------- nodejs


def test_nextjs_stack_and_scripts() -> None:
    profile = analyze("nextjs-app").profile
    assert profile.has_technology("Next.js")
    assert profile.has_technology("TypeScript")
    assert profile.has_technology("pnpm")
    assert profile.has_technology("Prisma")
    commands = command_texts(profile)
    assert "pnpm run dev" in commands
    assert "pnpm run build" in commands
    assert "pnpm run db:migrate" in commands
    assert any(database.migration_tool == "Prisma Migrate" for database in profile.databases)


def test_nextjs_package_script_evidence_points_at_package_json() -> None:
    profile = analyze("nextjs-app").profile
    dev = next(command for command in profile.commands if command.command == "pnpm run dev")
    assert dev.evidence[0].source == "package.json"
    assert dev.evidence[0].locator == "scripts.dev"
    assert dev.purpose is CommandPurpose.RUN


# -------------------------------------------------------------------------- go


def test_go_stack_and_commands() -> None:
    profile = analyze("go-app").profile
    assert profile.has_technology("Go")
    assert profile.has_technology("Gin")
    commands = command_texts(profile)
    assert "go test ./..." in commands
    assert "go build ./..." in commands
    assert "go mod download" in commands
    assert profile.apis and profile.apis[0].framework == "Gin"


# ---------------------------------------------------------------------- dotnet


def test_dotnet_stack_and_commands() -> None:
    profile = analyze("dotnet-app").profile
    assert profile.has_technology(".NET")
    assert profile.has_technology("ASP.NET Core")
    assert profile.has_technology("Entity Framework Core")
    commands = command_texts(profile)
    assert "dotnet test" in commands
    assert "dotnet build" in commands
    assert "dotnet ef database update" in commands
    assert any(database.orm == "Entity Framework Core" for database in profile.databases)


# --------------------------------------------------------------------- flutter


def test_flutter_stack_and_commands() -> None:
    profile = analyze("flutter-app").profile
    assert profile.has_technology("Flutter")
    assert profile.has_technology("Dart")
    commands = command_texts(profile)
    assert "flutter pub get" in commands
    assert "flutter test" in commands
    assert "flutter run" in commands
    assert "flutter build apk" in commands


# -------------------------------------------------------------------- monorepo


def test_monorepo_components_and_split_workflows() -> None:
    profile = analyze("monorepo-mixed").profile
    paths = {component.path for component in profile.components}
    assert {"services/api", "services/web"} <= paths
    run_workflows = profile.workflows_by_category("run")
    assert len(run_workflows) >= 2
    workflow_ids = {workflow.id for workflow in profile.workflows}
    assert len(workflow_ids) == len(profile.workflows), "workflow ids must be unique"


# --------------------------------------------------------------------- hostile


def test_hostile_repository_is_flagged_and_never_reads_secrets() -> None:
    result = analyze("hostile-repo")
    profile = result.profile
    risk_ids = {risk.id for risk in profile.risks}
    assert "committed-env-file" in risk_ids
    assert "destructive-commands" in risk_ids
    assert any(risk_id.startswith("prompt-injection-") for risk_id in risk_ids)
    assert any(risk_id.startswith("dangerous-lifecycle-") for risk_id in risk_ids)

    # The real .env is never read; the template is, but only key names are kept.
    assert result.scan.read(".env") is None
    assert "SAFE_VALUE" in profile.environment_keys
    assert all("AKIA" not in key for key in profile.environment_keys)

    # Destructive commands are detected, classified, and excluded from generated
    # skill scripts (see generator/validator tests) — never executed here.
    dangerous = [command for command in profile.commands if command.risk.is_dangerous]
    assert dangerous, "the hostile fixture's destructive commands must be detected"
    assert all(command.risk_reasons for command in dangerous)
    safe_commands = [command for command in profile.commands if command.risk.is_safe]
    assert "pytest -q" in {command.command for command in safe_commands}


def test_hostile_readme_content_is_not_treated_as_instructions() -> None:
    profile = analyze("hostile-repo").profile
    # The injection text is only ever quoted as redacted evidence.
    for risk in profile.risks:
        for evidence in risk.evidence:
            assert "sk-proj" not in evidence.snippet


# ------------------------------------------------------------------ invariants


@pytest.mark.parametrize(
    "fixture",
    [
        "fastapi-app",
        "nextjs-app",
        "go-app",
        "dotnet-app",
        "flutter-app",
        "monorepo-mixed",
        "hostile-repo",
    ],
)
def test_profile_invariants_hold_for_every_fixture(fixture: str) -> None:
    profile = analyze(fixture).profile
    assert profile.stats.total_files > 0
    assert profile.name == fixture
    # No command may be UNKNOWN certainty.
    assert all(command.certainty is not Certainty.UNKNOWN for command in profile.commands)
    # Every workflow references real commands and has a unique id.
    workflow_ids = [workflow.id for workflow in profile.workflows]
    assert len(workflow_ids) == len(set(workflow_ids))
    for workflow in profile.workflows:
        assert workflow.commands
        assert all(workflow.commands)
    # Findings never contain absolute local paths in text fields.
    for command in profile.commands:
        assert not command.command.startswith(("/", "C:\\", "D:\\"))


def test_analysis_is_deterministic() -> None:
    first = analyze("fastapi-app").profile
    second = analyze("fastapi-app").profile
    drop = {"generated_at", "root_path"}
    assert first.model_dump(exclude=drop) == second.model_dump(exclude=drop)


def test_workflow_categories_are_from_the_enum() -> None:
    profile = analyze("fastapi-app").profile
    for workflow in profile.workflows:
        assert workflow.category in set(WorkflowCategory)
