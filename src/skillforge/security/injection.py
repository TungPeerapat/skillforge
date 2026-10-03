"""Prompt-injection heuristics.

Repository files are untrusted input. SkillForge never treats their contents as
instructions, but instruction-like text still deserves a warning: it may be
picked up by a future LLM step or by the agent that later reads the repository.

Detection is heuristic and deliberately noisy in the "warn" direction: a false
positive costs one warning, a false negative could cost a compromised workflow.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from skillforge.utils.markdown import LineIndex

_EXCERPT_LIMIT = 160


class InjectionSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class InjectionSignal:
    """A suspicious instruction-like pattern found in repository content."""

    rule_id: str
    description: str
    severity: InjectionSeverity
    line: int
    excerpt: str


@dataclass(frozen=True)
class _InjectionRule:
    id: str
    description: str
    pattern: re.Pattern[str]
    severity: InjectionSeverity


_RULES: Final[tuple[_InjectionRule, ...]] = (
    _InjectionRule(
        "ignore-instructions",
        "Text tells the reader to ignore previous instructions",
        re.compile(
            r"(?i)\b(?:ignore|disregard|forget|override)\b[^.\n]{0,40}\b(?:previous|prior|above|earlier|all)\b[^.\n]{0,20}\b(?:instruction|prompt|rule|direction)s?\b"
        ),
        InjectionSeverity.HIGH,
    ),
    _InjectionRule(
        "role-override",
        "Text attempts to reassign the assistant's role",
        re.compile(
            r"(?i)\b(?:you are now|act as|pretend to be|new (?:system )?prompt|system prompt)\b"
        ),
        InjectionSeverity.HIGH,
    ),
    _InjectionRule(
        "secrecy-request",
        "Text asks the reader not to tell the user",
        re.compile(
            r"(?i)\b(?:do not|don't|never)\s+(?:tell|inform|notify|mention|reveal)\b[^.\n]{0,30}\b(?:user|human|developer|operator)\b"
        ),
        InjectionSeverity.HIGH,
    ),
    _InjectionRule(
        "exfiltration",
        "Text mentions sending files or secrets to an external location",
        re.compile(
            r"(?i)\b(?:exfiltrate|upload|send|post|transmit|leak)\b[^\n]{0,80}?"
            r"(?:\.env|secret|token|credential|ssh key|api[_-]?key|password|source code)"
        ),
        InjectionSeverity.HIGH,
    ),
    _InjectionRule(
        "encoded-payload",
        "Long base64-like blob may hide instructions",
        re.compile(r"(?:[A-Za-z0-9+/]{120,}={0,2})"),
        InjectionSeverity.LOW,
    ),
    _InjectionRule(
        "hidden-html-instruction",
        "HTML comment contains instruction-like text",
        re.compile(
            r"(?is)<!--(?=[^>]*\b(?:ignore|instead|must|always|never|run|execute)\b)[^>]{10,}?-->"
        ),
        InjectionSeverity.MEDIUM,
    ),
    _InjectionRule(
        "download-and-run",
        "Text instructs the reader to download and run a script",
        re.compile(r"(?i)\b(?:curl|wget|iwr|invoke-webrequest)\b[^\n]{0,160}\|\s*(?:ba|z|k)?sh\b"),
        InjectionSeverity.HIGH,
    ),
)

_MAX_EXCERPT = _EXCERPT_LIMIT


def scan_for_injection(text: str, *, max_signals: int = 50) -> list[InjectionSignal]:
    """Return instruction-like patterns found in untrusted text."""
    if not text:
        return []
    signals: list[InjectionSignal] = []
    index = LineIndex(text)
    for rule in _RULES:
        for match in rule.pattern.finditer(text):
            excerpt = match.group(0).replace("\n", " ").strip()
            if len(excerpt) > _MAX_EXCERPT:
                excerpt = excerpt[: _MAX_EXCERPT - 3] + "..."
            signals.append(
                InjectionSignal(
                    rule_id=rule.id,
                    description=rule.description,
                    severity=rule.severity,
                    line=index.line_of(match.start()),
                    excerpt=excerpt,
                )
            )
            if len(signals) >= max_signals:
                signals.sort(key=lambda item: item.line)
                return signals
    signals.sort(key=lambda item: item.line)
    return signals
