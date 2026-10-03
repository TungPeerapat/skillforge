"""Provider registry and consent enforcement.

Resolving a provider is the only place that decides whether repository content
may leave the machine. Default is ``none`` (fully local, deterministic).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from skillforge.config import Settings
from skillforge.errors import ExternalTransmissionError, UsageError
from skillforge.logging import get_logger
from skillforge.providers.base import LLMProvider
from skillforge.providers.http import (
    AnthropicProvider,
    OpenAICompatibleProvider,
    OpenRouterProvider,
    local_base_url_configured,
    read_api_key,
)
from skillforge.providers.mock import MockProvider

logger = get_logger("providers")

#: Providers that are fully local and never need consent.
LOCAL_PROVIDERS = frozenset({"none", "mock"})

#: Providers that always talk to a remote service.
NETWORK_PROVIDERS = frozenset({"openai", "anthropic", "openrouter", "openai-compatible"})


@dataclass(frozen=True)
class ProviderStatus:
    """Availability information for `skillforge doctor`."""

    id: str
    configured: bool
    available: bool
    detail: str = ""
    requires_network: bool = True


def known_providers() -> list[str]:
    return ["none", "mock", *sorted(NETWORK_PROVIDERS)]


def build_provider(provider_id: str, settings: Settings) -> LLMProvider:
    """Instantiate a provider by id from validated settings."""
    normalized = provider_id.strip().lower()
    if normalized in ("", "none"):
        raise UsageError("provider 'none' does not create a provider instance")
    if normalized == "mock":
        return MockProvider()
    provider_settings = settings.provider
    api_key_env = settings.provider_api_key_env()
    api_key = read_api_key(api_key_env)
    common: dict[str, Any] = {
        "api_key": api_key,
        "base_url": provider_settings.base_url,
        "model": provider_settings.model,
        "timeout": provider_settings.timeout_seconds,
        "api_key_env": api_key_env,
    }
    if normalized == "openai":
        return OpenAICompatibleProvider(provider_id="openai", **common)
    if normalized == "openrouter":
        return OpenRouterProvider(**common)
    if normalized == "anthropic":
        return AnthropicProvider(**common)
    if normalized in ("openai-compatible", "local"):
        if not provider_settings.base_url:
            raise UsageError(
                "provider 'openai-compatible' requires provider.base_url",
                hint="point it at your OpenAI-compatible endpoint (Ollama, vLLM, LM Studio, …)",
            )
        return OpenAICompatibleProvider(provider_id="openai-compatible", **common)
    raise UsageError(
        f"Unknown provider: '{provider_id}'",
        hint="known providers: " + ", ".join(known_providers()),
    )


def resolve_provider(
    settings: Settings,
    *,
    explicit: str | None = None,
    allow_external: bool = False,
) -> LLMProvider | None:
    """Resolve the provider to use, enforcing the transmission consent rule.

    Returns ``None`` when the deterministic path should be used.
    """
    requested = (explicit or settings.provider.default or "none").strip().lower()
    if requested in ("", "none"):
        return None
    if requested == "mock":
        return MockProvider()
    if requested not in NETWORK_PROVIDERS:
        raise UsageError(
            f"Unknown provider: '{requested}'",
            hint="known providers: " + ", ".join(known_providers()),
        )
    provider = build_provider(requested, settings)
    local = not provider.requires_network or local_base_url_configured(settings.provider.base_url)
    if not local and not (allow_external or settings.security.allow_external_transmission):
        raise ExternalTransmissionError(
            "Refusing to send repository context to an external provider.",
            hint=(
                "re-run with --allow-external-llm (or set "
                "security.allow_external_transmission = true) to consent to transmission"
            ),
        )
    logger.info(
        "provider enabled",
        extra={"provider": provider.id, "local": local, "model": provider.model},
    )
    return provider


def provider_status(settings: Settings) -> list[ProviderStatus]:
    """Availability of each known provider, without making network calls."""
    from skillforge.providers.http import _require_httpx

    statuses: list[ProviderStatus] = [
        ProviderStatus("none", True, True, "deterministic generation (default)", False),
        ProviderStatus("mock", True, True, "offline test provider", False),
    ]
    try:
        _require_httpx()
        httpx_available = True
    except Exception:
        httpx_available = False
    default = settings.provider.default.strip().lower()
    for provider_id in sorted(NETWORK_PROVIDERS):
        env_name = _env_for(provider_id, settings)
        key_present = bool(read_api_key(env_name))
        base_url = settings.provider.base_url
        local = provider_id == "openai-compatible" and local_base_url_configured(base_url)
        configured = default == provider_id
        if not httpx_available:
            detail = "httpx not installed (pip install 'skillforge[llm]')"
            available = False
        elif provider_id == "openai-compatible" and not base_url:
            detail = "provider.base_url is not configured"
            available = False
        elif local:
            detail = f"local endpoint {base_url}"
            available = True
        elif key_present:
            detail = f"{env_name} is set"
            available = True
        else:
            detail = f"{env_name} is not set"
            available = False
        statuses.append(ProviderStatus(provider_id, configured, available, detail, not local))
    return statuses


def _env_for(provider_id: str, settings: Settings) -> str:
    if settings.provider.api_key_env and settings.provider.default == provider_id:
        return settings.provider.api_key_env
    return {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "openai-compatible": "OPENAI_API_KEY",
    }.get(provider_id, "")


__all__ = [
    "LOCAL_PROVIDERS",
    "NETWORK_PROVIDERS",
    "ProviderStatus",
    "build_provider",
    "known_providers",
    "provider_status",
    "resolve_provider",
]
