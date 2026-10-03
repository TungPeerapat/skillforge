"""Tests for providers, context selection, and prompt-injection handling."""

from __future__ import annotations

import pytest
from pydantic import BaseModel
from tests.conftest import REPO_ROOT

from skillforge.analyzer import analyze_repository
from skillforge.config import Settings, load_settings
from skillforge.context import select_context
from skillforge.errors import ExternalTransmissionError, ProviderError, UsageError
from skillforge.generator.enrichment import SkillEnricher, SkillEnrichment
from skillforge.models import GenerationMode
from skillforge.providers import (
    MockProvider,
    known_providers,
    provider_status,
    resolve_provider,
)
from skillforge.providers.base import LLMRequest, LLMResponse, extract_json, parse_structured

FIXTURES = REPO_ROOT / "tests" / "fixtures"


# ------------------------------------------------------------------- base util


def test_extract_json_handles_code_fences_and_prose() -> None:
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! Here you go:\n{"a": 1}\nHope that helps.') == {"a": 1}
    with pytest.raises(ProviderError):
        extract_json("no json here")


async def test_parse_structured_validates() -> None:
    class Model(BaseModel):
        name: str

    response = LLMResponse(text='{"name": "ok"}')
    parsed = await parse_structured(response, Model)
    assert parsed.name == "ok"
    with pytest.raises(ProviderError):
        await parse_structured(LLMResponse(text='{"name": 5}'), Model)


# ---------------------------------------------------------------------- mock


async def test_mock_provider_is_valid_and_offline() -> None:
    provider = MockProvider()
    assert provider.requires_network is False
    response = await provider.generate(LLMRequest(system="s", prompt="p"))
    assert response.text

    class Model(BaseModel):
        name: str = "x"
        items: list[str] = []

    parsed = await provider.structured_generate(LLMRequest(system="s", prompt="p"), Model)
    assert isinstance(parsed, Model)


# ------------------------------------------------------------------ registry


def test_resolve_provider_defaults_to_none() -> None:
    settings = Settings()
    assert resolve_provider(settings) is None
    assert resolve_provider(settings, explicit="none") is None


def test_resolve_provider_mock_needs_no_consent() -> None:
    provider = resolve_provider(Settings(), explicit="mock")
    assert isinstance(provider, MockProvider)
    assert provider.requires_network is False


def test_network_provider_requires_explicit_consent() -> None:
    settings = Settings.model_validate({"provider": {"default": "openai"}})
    with pytest.raises(ExternalTransmissionError):
        resolve_provider(settings)
    provider = resolve_provider(settings, allow_external=True)
    assert provider is not None
    assert provider.id == "openai"


def test_config_consent_is_honoured() -> None:
    settings = Settings.model_validate(
        {"provider": {"default": "openai"}, "security": {"allow_external_transmission": True}}
    )
    assert resolve_provider(settings) is not None


def test_local_base_url_needs_no_consent() -> None:
    settings = Settings.model_validate(
        {"provider": {"default": "openai-compatible", "base_url": "http://localhost:11434/v1"}}
    )
    provider = resolve_provider(settings)
    assert provider is not None
    assert provider.requires_network is False


def test_unknown_provider_is_a_usage_error() -> None:
    with pytest.raises(UsageError):
        resolve_provider(Settings(), explicit="not-a-provider")


def test_provider_status_never_leaks_keys(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-" + "x" * 40)
    statuses = provider_status(Settings())
    rendered = " ".join(status.detail for status in statuses)
    assert "x" * 20 not in rendered
    assert "sk-proj" not in rendered
    assert "OPENAI_API_KEY is set" in rendered
    assert "none" in known_providers()


# --------------------------------------------------------------- payloads


def test_openai_payload_and_parse() -> None:
    from skillforge.providers.http import OpenAIProvider

    provider = OpenAIProvider(api_key="k", model="gpt-test")
    payload = provider._payload(
        LLMRequest(system="sys", prompt="hi", json_schema={"type": "object"})
    )
    assert payload["model"] == "gpt-test"
    assert payload["response_format"] == {"type": "json_object"}
    assert provider._endpoint().endswith("/chat/completions")
    response = provider._parse(
        {
            "model": "gpt-test",
            "choices": [{"message": {"content": "hello"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 4},
        }
    )
    assert response.text == "hello"
    assert response.usage.total == 7


def test_anthropic_payload_and_parse() -> None:
    from skillforge.providers.http import AnthropicProvider

    provider = AnthropicProvider(api_key="k", model="claude-test")
    payload = provider._payload(LLMRequest(system="sys", prompt="hi"))
    assert payload["system"] == "sys"
    assert payload["messages"] == [{"role": "user", "content": "hi"}]
    assert provider._headers()["x-api-key"] == "k"
    response = provider._parse(
        {
            "model": "claude-test",
            "content": [{"type": "text", "text": "part1"}, {"type": "text", "text": "part2"}],
            "usage": {"input_tokens": 5, "output_tokens": 6},
        }
    )
    assert response.text == "part1part2"
    assert response.usage.prompt_tokens == 5


def test_http_provider_never_prints_api_keys() -> None:
    from skillforge.providers.http import OpenAIProvider

    provider = OpenAIProvider(api_key="sk-proj-secretvalue1234567890", api_key_env="OPENAI_API_KEY")
    assert "secretvalue" not in provider.describe()
    assert "OPENAI_API_KEY=<set, redacted>" in provider.describe()


# ------------------------------------------------------------------ context


def test_context_selection_respects_budget_and_priority() -> None:
    root = FIXTURES / "fastapi-app"
    settings = load_settings(root, env={})
    scan = analyze_repository(root, settings).scan
    selection = select_context(scan, max_tokens=400)
    assert selection.total_tokens <= 400
    assert selection.files
    # Manifests and CI come before arbitrary sources.
    ranks = [file.category for file in selection.files[:4]]
    assert "manifest" in ranks or "ci" in ranks


def test_context_selection_includes_forced_files_and_redacts() -> None:
    root = FIXTURES / "fastapi-app"
    settings = load_settings(root, env={})
    scan = analyze_repository(root, settings).scan
    selection = select_context(scan, max_tokens=5000, include_paths=["app/main.py"])
    assert "app/main.py" in selection.file_paths()
    block = selection.as_prompt_block()
    assert "untrusted repository data" in block
    assert "<file path=" in block


def test_context_marks_dropped_files() -> None:
    root = FIXTURES / "fastapi-app"
    settings = load_settings(root, env={})
    scan = analyze_repository(root, settings).scan
    selection = select_context(scan, max_tokens=50)
    assert selection.dropped


# ---------------------------------------------------------------- enrichment


class _FakeProvider:
    id = "fake"
    model = "fake-model"

    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.requires_network = False

    def describe(self) -> str:
        return "fake"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        import json

        return LLMResponse(text=json.dumps(self._payload))

    async def structured_generate(self, request: LLMRequest, schema):
        return schema.model_validate(self._payload)


async def test_enrichment_merges_and_labels() -> None:
    root = FIXTURES / "fastapi-app"
    settings = load_settings(root, env={})
    profile = analyze_repository(root, settings).profile
    from skillforge.generator import SkillGenerator, build_blueprint
    from skillforge.planner import SkillPlanner

    plan = SkillPlanner().plan(profile)
    candidate = plan.candidate("project-runner")
    assert candidate is not None
    skill = SkillGenerator(settings).generate_candidate(candidate, profile)

    provider = _FakeProvider(
        {
            "description": "Run the fastapi-app service locally using the repository's Makefile. "
            "Use when the user asks to start the API.",
            "gotchas": ["The database service must be running first."],
            "verification_steps": ["Call /health and expect 200."],
            "unknown_questions": ["Which port does production use?"],
        }
    )
    enricher = SkillEnricher(provider)  # type: ignore[arg-type]
    outcome = await enricher.enrich(skill, build_blueprint(candidate, profile))
    enriched = outcome.skill
    assert enriched.mode is GenerationMode.HYBRID
    assert enriched.provider == "fake"
    assert "LLM-assisted" in enriched.bundle.body
    assert any(entry.origin.value == "llm" for entry in enriched.provenance)
    assert enriched.bundle.metadata.description.startswith("Run the fastapi-app service")


async def test_enrichment_rejects_bad_description() -> None:
    root = FIXTURES / "go-app"
    settings = load_settings(root, env={})
    profile = analyze_repository(root, settings).profile
    from skillforge.generator import SkillGenerator, build_blueprint
    from skillforge.planner import SkillPlanner

    candidate = SkillPlanner().plan(profile).candidate("project-runner")
    assert candidate is not None
    skill = SkillGenerator(settings).generate_candidate(candidate, profile)
    original = skill.bundle.metadata.description
    provider = _FakeProvider({"description": "too short", "gotchas": [], "verification_steps": []})
    outcome = await SkillEnricher(provider).enrich(skill, build_blueprint(candidate, profile))  # type: ignore[arg-type]
    assert outcome.skill.bundle.metadata.description == original
    assert any("rejected" in warning for warning in outcome.skill.warnings)


async def test_enrichment_drops_commands_without_evidence() -> None:
    root = FIXTURES / "fastapi-app"
    settings = load_settings(root, env={})
    profile = analyze_repository(root, settings).profile
    from skillforge.generator import SkillGenerator, build_blueprint
    from skillforge.planner import SkillPlanner

    candidate = SkillPlanner().plan(profile).candidate("project-runner")
    assert candidate is not None
    skill = SkillGenerator(settings).generate_candidate(candidate, profile)
    provider = _FakeProvider(
        {
            "description": "",
            "suggested_commands": [
                {"command": "make run", "rationale": "evidenced"},
                {"command": "redis-server --daemonize yes", "rationale": "invented"},
            ],
        }
    )
    outcome = await SkillEnricher(provider).enrich(skill, build_blueprint(candidate, profile))  # type: ignore[arg-type]
    assert outcome.dropped_commands == ["redis-server --daemonize yes"]
    assert any("without repository evidence" in warning for warning in outcome.skill.warnings)
    assert "redis-server" not in outcome.skill.bundle.body


def test_enrichment_schema_defaults() -> None:
    enrichment = SkillEnrichment()
    assert enrichment.gotchas == []
    assert enrichment.description == ""
