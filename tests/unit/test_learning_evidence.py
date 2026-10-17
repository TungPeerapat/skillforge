"""Unit tests for readiness evidence extraction (no fabrication of /health)."""

from __future__ import annotations

from datetime import UTC, datetime

from skillforge.learning.evidence import (
    build_readiness,
    find_bare_health_path,
    find_documented_endpoints,
    find_port,
)
from skillforge.models import (
    Command,
    Evidence,
    ReadinessConfidence,
    ReadinessKind,
    RepositoryProfile,
    TechnologyKind,
)
from skillforge.models.repository import DetectedTechnology
from skillforge.models.workflow import CommandPurpose, Service, ServiceKind


def _profile(
    *, commands: list[Command] | None = None, technologies: list[DetectedTechnology] | None = None
) -> RepositoryProfile:
    return RepositoryProfile(
        generated_at=datetime.now(UTC),
        name="demo",
        root_path=".",
        commands=commands or [],
        technologies=technologies or [],
    )


def _command(text: str, *, source: str = "README.md") -> Command:
    return Command(
        command=text,
        purpose=CommandPurpose.RUN,
        risk_reasons=[],
        evidence=[Evidence(source=source, weight=0.8)],
    )


def test_documented_endpoint_is_evidenced() -> None:
    contents = {
        "README.md": "## Verify\ncurl http://localhost:8123/health\n",
    }
    endpoints = find_documented_endpoints(contents)
    assert endpoints
    assert endpoints[0].url == "http://127.0.0.1:8123/health"

    plan = build_readiness(_profile(), contents, [_command("python -m app")])
    assert plan.kind is ReadinessKind.HTTP
    assert plan.confidence is ReadinessConfidence.EVIDENCED
    assert plan.url == "http://127.0.0.1:8123/health"
    assert plan.evidence


def test_health_endpoint_is_never_fabricated() -> None:
    contents = {"README.md": "## Run\npython -m app\n"}
    plan = build_readiness(_profile(), contents, [_command("python -m app")])
    assert plan.kind is not ReadinessKind.HTTP
    assert plan.kind is ReadinessKind.LOG_SIGNAL


def test_non_health_path_does_not_produce_http_readiness() -> None:
    contents = {"README.md": "curl http://localhost:8000/api/items\n"}
    assert find_bare_health_path(contents) is None
    plan = build_readiness(_profile(), contents, [_command("python -m app")])
    assert plan.kind is ReadinessKind.TCP
    assert plan.port == 8000
    assert "not a readiness probe" in plan.detail


def test_readiness_path_is_preferred_over_other_urls() -> None:
    contents = {
        "README.md": ("curl http://localhost:8000/api/items\ncurl http://localhost:8000/health\n")
    }
    plan = build_readiness(_profile(), contents, [_command("python -m app")])
    assert plan.kind is ReadinessKind.HTTP
    assert plan.url == "http://127.0.0.1:8000/health"


def test_root_path_counts_as_a_readiness_probe() -> None:
    contents = {"README.md": "curl http://localhost:3000/\n"}
    plan = build_readiness(_profile(), contents, [_command("npm run dev")])
    assert plan.kind is ReadinessKind.HTTP
    assert plan.path == "/"


def test_bare_health_path_with_port_flag_is_inferred() -> None:
    contents = {"README.md": "The API exposes GET /health for checks.\n"}
    commands = [_command("python -m app --port 9000")]
    plan = build_readiness(_profile(commands=commands), contents, commands)
    assert plan.kind is ReadinessKind.HTTP
    assert plan.confidence is ReadinessConfidence.INFERRED
    assert plan.url == "http://127.0.0.1:9000/health"


def test_route_definition_is_endpoint_evidence() -> None:
    contents = {"app/main.py": '@app.get("/health")\ndef health():\n    return {}\n'}
    technologies = [
        DetectedTechnology(kind=TechnologyKind.FRAMEWORK, name="FastAPI", confidence=0.9)
    ]
    plan = build_readiness(
        _profile(technologies=technologies), contents, [_command("python -m app")]
    )
    assert plan.kind is ReadinessKind.HTTP
    assert plan.port == 8000
    assert plan.confidence is ReadinessConfidence.INFERRED


def test_port_from_env_template() -> None:
    contents = {".env.example": "PORT=9100\n"}
    profile = _profile()
    profile.environment_files = [".env.example"]
    port, source = find_port(profile, contents, [])
    assert port == 9100
    assert source == ".env.example"


def test_port_from_compose_service() -> None:
    service = Service(
        name="web",
        kind=ServiceKind.WEB,
        ports=["8085:8000"],
        evidence=[Evidence(source="compose.yaml", locator="services.web")],
    )
    profile = _profile()
    profile.services = [service]
    port, source = find_port(profile, {}, [])
    assert port == 8085
    assert "compose.yaml" in source


def test_port_from_framework_default() -> None:
    technologies = [
        DetectedTechnology(kind=TechnologyKind.FRAMEWORK, name="FastAPI", confidence=0.9)
    ]
    port, source = find_port(_profile(technologies=technologies), {}, [])
    assert port == 8000
    assert source == "framework default"


def test_port_only_falls_back_to_tcp() -> None:
    contents = {"README.md": "The server listens on port 8123.\n"}
    commands = [_command("python -m app --port 8123")]
    plan = build_readiness(_profile(commands=commands), contents, commands)
    assert plan.kind is ReadinessKind.TCP
    assert plan.port == 8123
