"""HTTP providers: OpenAI-compatible chat completions and Anthropic messages.

``httpx`` is imported lazily so the deterministic core works without the optional
``skillforge[llm]`` extra installed.
"""

from __future__ import annotations

import json
import os
from typing import Any, TypeVar

from pydantic import BaseModel

from skillforge.errors import ProviderError
from skillforge.providers.base import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
    TokenUsage,
    parse_structured,
    schema_hint,
)
from skillforge.security.redaction import mask_env_value

SchemaT = TypeVar("SchemaT", bound=BaseModel)

_DEFAULT_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}

_JSON_INSTRUCTION = (
    "Respond with a single JSON object and nothing else. "
    "It must validate against this JSON schema:\n{schema}"
)


def _require_httpx() -> Any:
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - depends on extras
        raise ProviderError(
            "The HTTP client is not installed.",
            hint="install the LLM extra: pip install 'skillforge[llm]'",
        ) from exc
    return httpx


def _is_local_url(url: str) -> bool:
    lowered = url.lower()
    return any(
        marker in lowered
        for marker in ("localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal")
    )


class HTTPProvider(LLMProvider):
    """Shared plumbing for JSON-over-HTTP providers."""

    id = "http"
    model = ""

    def __init__(
        self,
        *,
        api_key: str = "",
        base_url: str = "",
        model: str = "",
        timeout: float = 60.0,
        api_key_env: str = "",
    ) -> None:
        self._api_key = api_key
        self._base_url = (base_url or self.default_base_url).rstrip("/")
        self.model = model or self.default_model
        self._timeout = timeout
        self._api_key_env = api_key_env

    # ------------------------------------------------------------ subclass API
    @property
    def default_base_url(self) -> str:
        return ""

    @property
    def default_model(self) -> str:
        return ""

    def _endpoint(self) -> str:
        raise NotImplementedError

    def _headers(self) -> dict[str, str]:
        raise NotImplementedError

    def _payload(self, request: LLMRequest) -> dict[str, Any]:
        raise NotImplementedError

    def _parse(self, data: dict[str, Any]) -> LLMResponse:
        raise NotImplementedError

    # ------------------------------------------------------------- public API
    @property
    def requires_network(self) -> bool:
        return not _is_local_url(self._base_url)

    def describe(self) -> str:
        key_state = mask_env_value(self._api_key_env or "api_key", self._api_key)
        return f"{self.id} ({self.model}) at {self._base_url}; {key_state}"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        httpx = _require_httpx()
        if self.requires_network and not self._api_key:
            raise ProviderError(
                f"No API key available for provider '{self.id}'.",
                hint=f"set {self._api_key_env or 'the provider API key'} in the environment",
            )
        payload = self._payload(request)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    self._endpoint(), headers=self._headers(), json=payload
                )
        except Exception as exc:
            raise ProviderError(f"{self.id} request failed: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            snippet = response.text[:300].replace("\n", " ")
            raise ProviderError(
                f"{self.id} returned HTTP {response.status_code}: {snippet}",
                hint="check the model name, API key, and endpoint",
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError(f"{self.id} returned a non-JSON response") from exc
        return self._parse(data)

    async def structured_generate(self, request: LLMRequest, schema: type[SchemaT]) -> SchemaT:
        prompt = request.prompt + "\n\n" + _JSON_INSTRUCTION.format(schema=schema_hint(schema))
        attempts = 0
        last_error: ProviderError | None = None
        while attempts < 2:
            attempts += 1
            response = await self.generate(
                LLMRequest(
                    system=request.system,
                    prompt=prompt,
                    purpose=request.purpose,
                    max_output_tokens=request.max_output_tokens,
                    temperature=request.temperature,
                    json_schema=schema.model_json_schema(),
                )
            )
            try:
                parsed = await parse_structured(response, schema)
            except ProviderError as exc:
                last_error = exc
                prompt = (
                    request.prompt
                    + "\n\nYour previous response was rejected: "
                    + str(exc)
                    + "\nReply with corrected JSON only."
                )
                continue
            return parsed.model_copy(update={}) if False else parsed
        raise last_error or ProviderError("provider did not return valid structured output")


class OpenAICompatibleProvider(HTTPProvider):
    """OpenAI ``/chat/completions`` — also used for OpenRouter and local servers."""

    id = "openai-compatible"

    def __init__(self, *, provider_id: str = "openai-compatible", **kwargs: Any) -> None:
        self.id = provider_id
        super().__init__(**kwargs)

    @property
    def default_base_url(self) -> str:
        return _DEFAULT_BASE_URLS.get(self.id, "https://api.openai.com/v1")

    @property
    def default_model(self) -> str:
        return "gpt-4.1-mini"

    def _endpoint(self) -> str:
        return f"{self._base_url}/chat/completions"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        if self.id == "openrouter":
            headers["HTTP-Referer"] = "https://github.com/skillforge/skillforge"
            headers["X-Title"] = "SkillForge"
        return headers

    def _payload(self, request: LLMRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.prompt},
            ],
            "max_tokens": request.max_output_tokens,
            "temperature": request.temperature,
        }
        if request.json_schema is not None:
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _parse(self, data: dict[str, Any]) -> LLMResponse:
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("unexpected chat-completions response shape") from exc
        usage = data.get("usage") or {}
        return LLMResponse(
            text=str(text),
            model=str(data.get("model") or self.model),
            provider=self.id,
            usage=TokenUsage(
                prompt_tokens=int(usage.get("prompt_tokens") or 0),
                completion_tokens=int(usage.get("completion_tokens") or 0),
            ),
        )


class OpenAIProvider(OpenAICompatibleProvider):
    id = "openai"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(provider_id="openai", **kwargs)


class OpenRouterProvider(OpenAICompatibleProvider):
    id = "openrouter"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(provider_id="openrouter", **kwargs)
        self.model = self.model or "openai/gpt-4.1-mini"


class AnthropicProvider(HTTPProvider):
    """Anthropic ``/v1/messages``."""

    id = "anthropic"

    @property
    def default_base_url(self) -> str:
        return "https://api.anthropic.com"

    @property
    def default_model(self) -> str:
        return "claude-sonnet-4-5"

    def _endpoint(self) -> str:
        return f"{self._base_url}/v1/messages"

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
        }

    def _payload(self, request: LLMRequest) -> dict[str, Any]:
        return {
            "model": self.model,
            "system": request.system,
            "messages": [{"role": "user", "content": request.prompt}],
            "max_tokens": request.max_output_tokens,
            "temperature": request.temperature,
        }

    def _parse(self, data: dict[str, Any]) -> LLMResponse:
        content = data.get("content")
        if not isinstance(content, list):
            raise ProviderError("unexpected Anthropic response shape")
        text = "".join(str(block.get("text", "")) for block in content if isinstance(block, dict))
        usage = data.get("usage") or {}
        return LLMResponse(
            text=text,
            model=str(data.get("model") or self.model),
            provider=self.id,
            usage=TokenUsage(
                prompt_tokens=int(usage.get("input_tokens") or 0),
                completion_tokens=int(usage.get("output_tokens") or 0),
            ),
        )


def local_base_url_configured(base_url: str) -> bool:
    """True when a base URL points at this machine (no consent required)."""
    return _is_local_url(base_url)


def read_api_key(env_name: str) -> str:
    """Read a provider key from the environment (never from a file we control)."""
    if not env_name:
        return ""
    return os.environ.get(env_name, "").strip()


def json_dumps(payload: Any) -> str:
    """Small helper used by tests to build fake provider responses."""
    return json.dumps(payload)
