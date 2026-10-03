"""Provider abstraction.

Business logic depends on this protocol, never on a vendor SDK. Adapters are
thin: build a request, parse a response, raise :class:`ProviderError` on failure.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel, ValidationError

from skillforge.errors import ProviderError

SchemaT = TypeVar("SchemaT", bound=BaseModel)


@dataclass(frozen=True)
class LLMRequest:
    """One provider call.

    ``purpose`` is used for logging and tracing; ``json_schema`` is a pydantic
    JSON schema when structured output is required.
    """

    system: str
    prompt: str
    purpose: str = "generic"
    max_output_tokens: int = 2048
    temperature: float = 0.0
    json_schema: dict[str, Any] | None = None


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str = ""
    provider: str = ""
    usage: TokenUsage = field(default_factory=TokenUsage)
    attempts: int = 1


@runtime_checkable
class LLMProvider(Protocol):
    """Minimal provider interface (async, because HTTP is async)."""

    id: str
    model: str

    async def generate(self, request: LLMRequest) -> LLMResponse:
        """Return free-form text for the request."""

    async def structured_generate(self, request: LLMRequest, schema: type[SchemaT]) -> SchemaT:
        """Return a validated instance of ``schema``."""

    def describe(self) -> str:
        """Human-readable description for `doctor` and logs."""

    @property
    def requires_network(self) -> bool:
        """True when calls leave the machine (needs explicit consent)."""


_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def extract_json(text: str) -> Any:
    """Extract the first JSON object from a model response."""
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```[a-zA-Z]*\n?", "", candidate)
        candidate = re.sub(r"\n?```$", "", candidate).strip()
    try:
        return json.loads(candidate)
    except ValueError:
        pass
    match = _JSON_BLOCK_RE.search(candidate)
    if match is None:
        raise ProviderError("provider response did not contain a JSON object")
    try:
        return json.loads(match.group(0))
    except ValueError as exc:
        raise ProviderError(f"provider returned invalid JSON: {exc}") from exc


async def parse_structured[SchemaT: BaseModel](
    response: LLMResponse, schema: type[SchemaT]
) -> SchemaT:
    """Validate a provider response against a pydantic schema."""
    payload = extract_json(response.text)
    try:
        return schema.model_validate(payload)
    except ValidationError as exc:
        raise ProviderError(
            f"provider response did not match the expected schema: {exc.error_count()} error(s)",
            hint="the response was rejected rather than blindly trusted",
        ) from exc


def schema_hint(schema: type[BaseModel]) -> str:
    """Render a JSON schema for inclusion in a prompt."""
    return json.dumps(schema.model_json_schema(), indent=2, ensure_ascii=False)
