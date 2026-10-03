"""Redaction of sensitive strings before they reach logs or artifacts."""

from __future__ import annotations

import re

from skillforge.security.secrets import SECRET_RULES

# Environment variables whose *values* must never be printed.
SENSITIVE_ENV_HINTS = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "AUTH",
    "COOKIE",
    "SESSION",
    "PRIVATE",
)

_ASSIGNMENT_RE = re.compile(
    r"""\b(?P<name>[A-Za-z_][A-Za-z0-9_]{0,64})\s*(?P<sep>[:=])\s*(?P<value>\S{6,})"""
)


def is_sensitive_name(name: str) -> bool:
    """True when an identifier looks like it holds a secret."""
    upper = name.upper()
    return any(hint in upper for hint in SENSITIVE_ENV_HINTS)


def redact_text(text: str, *, max_length: int | None = None) -> str:
    """Return ``text`` with detected secrets replaced by ``[redacted]``.

    The function is deterministic and idempotent: redacting already-redacted
    text is a no-op.
    """
    if not text:
        return text
    result = text
    if max_length is not None and len(result) > max_length:
        result = result[:max_length]
    for rule in SECRET_RULES:
        if rule.id == "generic-secret-assignment":
            continue
        result = rule.pattern.sub("[redacted]", result)

    # Sensitive variable assignments: KEY=value / api_key: value
    def _replace(match: re.Match[str]) -> str:
        name = match.group("name")
        if is_sensitive_name(name) and match.group("value") not in ("[redacted]",):
            return f"{name}{match.group('sep')}[redacted]"
        return match.group(0)

    return _ASSIGNMENT_RE.sub(_replace, result)


def mask_env_value(name: str, value: str | None) -> str:
    """Describe an environment variable without revealing its value."""
    if value is None or value == "":
        return f"{name}=<unset>"
    if is_sensitive_name(name):
        return f"{name}=<set, redacted>"
    return f"{name}={value}"
