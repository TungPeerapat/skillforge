"""Optional live provider test.

Skipped unless ``OPENAI_API_KEY`` is set and the ``llm`` marker is selected:

    OPENAI_API_KEY=… pytest -m llm

The default test run (`pytest`) deselects this module's marker by not selecting
it; it still collects and skips.
"""

from __future__ import annotations

import os

import pytest

from skillforge.config import Settings
from skillforge.providers import resolve_provider
from skillforge.providers.base import LLMRequest

pytestmark = pytest.mark.llm


@pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="OPENAI_API_KEY is not set")
async def test_live_openai_structured_output() -> None:
    from pydantic import BaseModel

    class Summary(BaseModel):
        title: str

    settings = Settings.model_validate(
        {
            "provider": {
                "default": "openai",
                "model": os.environ.get("SKILLFORGE_TEST_MODEL", "gpt-4.1-mini"),
            },
            "security": {"allow_external_transmission": True},
        }
    )
    provider = resolve_provider(settings, allow_external=True)
    assert provider is not None
    result = await provider.structured_generate(
        LLMRequest(
            system="Reply with JSON only.",
            prompt='Return a JSON object with title set to "ok".',
            purpose="live-test",
            max_output_tokens=50,
        ),
        Summary,
    )
    assert result.title
