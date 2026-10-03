"""Approximate token accounting.

SkillForge deliberately avoids shipping a model-specific tokenizer: doing so
would add a heavy dependency and would still be wrong for whichever model the
user actually runs. Instead we use the widely used ``characters / 4``
approximation, which is good enough for budgeting and is clearly reported as an
estimate in output and documentation.

If exact counting is ever required, a tokenizer backend can be plugged in here
without changing call sites.
"""

from __future__ import annotations

import math

CHARS_PER_TOKEN = 4.0


def estimate_tokens(text: str) -> int:
    """Estimate the number of tokens in ``text`` (approximation)."""
    if not text:
        return 0
    return max(1, math.ceil(len(text) / CHARS_PER_TOKEN))


def estimate_tokens_for_files(contents: list[str]) -> int:
    """Estimate tokens for several documents plus a small per-file overhead."""
    return sum(estimate_tokens(content) + 4 for content in contents)


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Trim ``text`` so that its estimate is at most ``max_tokens``."""
    if max_tokens <= 0:
        return ""
    max_chars = int(max_tokens * CHARS_PER_TOKEN)
    if len(text) <= max_chars:
        return text
    return text[:max_chars]
