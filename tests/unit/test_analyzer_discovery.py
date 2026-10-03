"""Tests for file classification and command parsing."""

from __future__ import annotations

import pytest

from skillforge.analyzer.languages import (
    FileCategory,
    category_for,
    is_ci_path,
    is_doc_path,
    is_test_path,
    language_for,
)
from skillforge.discovery.commands import (
    build_command,
    classify_purpose,
    commands_from_markdown,
    extract_placeholders,
    parse_makefile,
    procfile_commands,
    script_command,
    unique_commands,
)
from skillforge.models.common import RiskLevel
from skillforge.models.workflow import CommandPurpose, CommandSource


@pytest.mark.parametrize(
    ("path", "language"),
    [
        ("src/app/main.py", "python"),
        ("web/index.tsx", "typescript"),
        ("go.mod", "unknown"),
        ("main.go", "go"),
        ("App.cs", "csharp"),
        ("lib/main.dart", "dart"),
        ("docker/Dockerfile", "dockerfile"),
        ("Makefile", "make"),
        ("README.md", "markdown"),
        ("unknown.zzz", "unknown"),
    ],
)
def test_language_detection(path: str, language: str) -> None:
    assert language_for(path) == language


@pytest.mark.parametrize(
    ("path", "category"),
    [
        ("pyproject.toml", FileCategory.MANIFEST),
        ("requirements-dev.txt", FileCategory.MANIFEST),
        ("uv.lock", FileCategory.LOCKFILE),
        ("package-lock.json", FileCategory.LOCKFILE),
        ("Dockerfile", FileCategory.INFRASTRUCTURE),
        ("docker-compose.yml", FileCategory.INFRASTRUCTURE),
        ("Makefile", FileCategory.INFRASTRUCTURE),
        (".github/workflows/ci.yml", FileCategory.CI),
        ("alembic/versions/0001_init.py", FileCategory.DATABASE),
        ("migrations/0001.sql", FileCategory.DATABASE),
        ("docs/guide.md", FileCategory.DOCS),
        ("README.md", FileCategory.DOCS),
        ("tests/test_app.py", FileCategory.TEST),
        ("app/main.py", FileCategory.SOURCE),
        (".env.example", FileCategory.ENV_TEMPLATE),
        ("LICENSE", FileCategory.LICENSE),
        ("tsconfig.json", FileCategory.CONFIG),
    ],
)
def test_category_detection(path: str, category: FileCategory) -> None:
    assert category_for(path) is category


def test_test_and_doc_path_helpers() -> None:
    assert is_test_path("tests/test_app.py")
    assert is_test_path("src/app.spec.ts")
    assert not is_test_path("src/app.py")
    assert is_doc_path("docs/architecture.md")
    assert is_doc_path("CONTRIBUTING.md")
    assert not is_doc_path("src/main.py")
    assert is_ci_path(".github/workflows/release.yml")
    assert is_ci_path(".gitlab-ci.yml")
    assert not is_ci_path("src/ci.py")


@pytest.mark.parametrize(
    ("name", "body", "expected"),
    [
        ("test", "pytest -q", CommandPurpose.TEST),
        ("test:unit", "jest", CommandPurpose.TEST),
        ("build", "tsc -b", CommandPurpose.BUILD),
        ("lint", "eslint .", CommandPurpose.LINT),
        ("format", "prettier --write .", CommandPurpose.FORMAT),
        ("typecheck", "tsc --noEmit", CommandPurpose.TYPECHECK),
        ("db:migrate", "prisma migrate deploy", CommandPurpose.MIGRATE),
        ("migrate", "./manage.py migrate", CommandPurpose.MIGRATE),
        ("dev", "next dev", CommandPurpose.RUN),
        ("start", "uvicorn app:app", CommandPurpose.RUN),
        ("install", "uv sync", CommandPurpose.SETUP),
        ("seed", "python manage.py loaddata", CommandPurpose.SEED),
        ("deploy", "kubectl apply -f k8s/", CommandPurpose.DEPLOY),
        ("clean", "rm -rf dist", CommandPurpose.CLEAN),
        ("mystery", "pytest -q", CommandPurpose.TEST),
        ("mystery", "uvicorn app:app", CommandPurpose.RUN),
        ("mystery", "", CommandPurpose.OTHER),
    ],
)
def test_purpose_classification(name: str, body: str, expected: CommandPurpose) -> None:
    assert classify_purpose(name, body) is expected


def test_script_command_uses_detected_manager() -> None:
    assert script_command("pnpm", "dev") == "pnpm run dev"
    assert script_command("yarn", "build") == "yarn run build"
    assert script_command("weird", "test") == "npm run test"


def test_parse_makefile_extracts_targets_and_recipes() -> None:
    text = (
        "# comment\n"
        "CC = gcc\n"
        ".PHONY: build test\n\n"
        "build:\n"
        "\tgo build -o bin/app .\n\n"
        "test: build\n"
        "\tgo test ./...\n\n"
        "%.o: %.c\n"
        "\t$(CC) -c $<\n"
    )
    targets = parse_makefile(text)
    names = [target.name for target in targets]
    assert names == ["build", "test"]
    assert "go test" in targets[1].recipe
    assert targets[1].phony is True


def test_commands_from_markdown_only_accepts_known_tooling() -> None:
    text = (
        "# Project\n\n"
        "```bash\n"
        "npm install\n"
        "npm run dev\n"
        "curl https://example.com/data.json -o data.json\n"
        "echo 'this is prose, not a command'\n"
        "cd examples\n"
        "```\n"
    )
    commands = commands_from_markdown("README.md", text)
    texts = [command.command for command in commands]
    assert texts == ["npm install", "npm run dev"]
    assert all(command.evidence for command in commands)
    assert all(command.source is CommandSource.README for command in commands)


def test_commands_from_markdown_ignores_non_shell_blocks() -> None:
    text = '```json\n{\n  "name": "pytest"\n}\n```\n'
    assert commands_from_markdown("README.md", text) == []


def test_procfile_commands() -> None:
    commands = procfile_commands(
        "Procfile", "web: uvicorn app.main:app\nworker: celery -A app worker\n"
    )
    assert [command.command for command in commands] == [
        "uvicorn app.main:app",
        "celery -A app worker",
    ]
    assert all(command.purpose is CommandPurpose.RUN for command in commands)


def test_build_command_attaches_risk_and_evidence() -> None:
    command = build_command(
        "docker compose up -d",
        source=CommandSource.COMPOSE,
        path="docker-compose.yml",
        locator="services",
    )
    assert command.risk is RiskLevel.REVIEW
    assert command.evidence[0].label == "docker-compose.yml:services"
    dangerous = build_command("rm -rf /", source=CommandSource.README, path="README.md")
    assert dangerous.risk is RiskLevel.DANGEROUS


def test_extract_placeholders() -> None:
    assert extract_placeholders("curl http://localhost:${PORT}/x") == ["${PORT}"]
    assert extract_placeholders("deploy --tag <version>") == ["<version>"]
    assert extract_placeholders("cd ${{ matrix.dir }} && pytest") == ["${{ matrix.dir }}"]
    assert extract_placeholders("pytest -q") == []


def test_unique_commands_dedupes_by_command_and_cwd() -> None:
    first = build_command("pytest", source=CommandSource.CONFIG, path="pyproject.toml")
    second = build_command("pytest", source=CommandSource.CI, path=".github/workflows/ci.yml")
    third = build_command(
        "pytest", source=CommandSource.CONFIG, path="sub/pyproject.toml", cwd="sub"
    )
    assert len(unique_commands([first, second, third])) == 2
