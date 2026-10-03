"""Security primitives: secret detection, redaction, path and command safety."""

from __future__ import annotations

from skillforge.security.command_risk import CommandRisk, RiskLevel, classify_command
from skillforge.security.injection import InjectionSignal, scan_for_injection
from skillforge.security.paths import (
    PathViolation,
    assert_safe_relative,
    is_within,
    safe_join,
    safe_relative,
)
from skillforge.security.redaction import redact_text
from skillforge.security.secrets import (
    SecretFinding,
    find_secrets,
    looks_like_placeholder,
)

__all__ = [
    "CommandRisk",
    "InjectionSignal",
    "PathViolation",
    "RiskLevel",
    "SecretFinding",
    "assert_safe_relative",
    "classify_command",
    "find_secrets",
    "is_within",
    "looks_like_placeholder",
    "redact_text",
    "safe_join",
    "safe_relative",
    "scan_for_injection",
]
