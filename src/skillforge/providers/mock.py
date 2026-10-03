"""Deterministic mock provider used by tests and by ``--provider mock``.

It never performs I/O and always returns schema-valid, obviously-synthetic
content. It exists so that the LLM-assisted code path can be exercised (and
demonstrated) without an API key.
"""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

from skillforge.providers.base import LLMProvider, LLMRequest, LLMResponse, TokenUsage

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class MockProvider(LLMProvider):
    """A provider that returns canned, schema-valid responses."""

    id = "mock"
    model = "mock-deterministic"

    def __init__(self, *, reply: str | None = None) -> None:
        self._reply = reply

    @property
    def requires_network(self) -> bool:
        return False

    def describe(self) -> str:
        return "mock provider (no network, deterministic output)"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        text = self._reply if self._reply is not None else '{"note": "mock provider response"}'
        return LLMResponse(
            text=text,
            model=self.model,
            provider=self.id,
            usage=TokenUsage(
                prompt_tokens=len(request.prompt) // 4, completion_tokens=len(text) // 4
            ),
        )

    async def structured_generate(self, request: LLMRequest, schema: type[SchemaT]) -> SchemaT:
        """Return a minimal valid instance built from the schema's defaults."""
        payload = _minimal_payload(schema)
        return schema.model_validate(payload)


def _minimal_payload(schema: type[BaseModel]) -> dict[str, object]:
    """Build a minimally valid payload from the schema definition."""
    payload: dict[str, object] = {}
    for name, field in schema.model_fields.items():
        if not field.is_required() and field.default is None:
            continue
        annotation = field.annotation
        if annotation is str or annotation == (str | None):
            payload[name] = f"mock {name}"
        elif annotation is int:
            payload[name] = 0
        elif annotation is float:
            payload[name] = 0.0
        elif annotation is bool:
            payload[name] = False
        else:
            payload[name] = []
    return payload
