"""Secret detection.

Design rules:

* Detection is pattern based and deterministic; no network calls, no entropy
  scoring of arbitrary text (which produces too many false positives on
  lockfiles and hashes).
* Matched values are **never** returned in full. Callers receive a redacted
  preview and an offset only.
* Placeholder values (``${API_KEY}``, ``<your-key>``, ``changeme`` …) are
  ignored so that ``.env.example`` files can be analysed safely.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from skillforge.utils.markdown import LineIndex

REDACTED = "[redacted]"


class SecretSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True)
class SecretRule:
    """One named secret-detection rule."""

    id: str
    description: str
    pattern: re.Pattern[str]
    severity: SecretSeverity
    group: int = 1


@dataclass(frozen=True)
class SecretFinding:
    """A single secret match, safe to print or serialize."""

    rule_id: str
    description: str
    severity: SecretSeverity
    line: int
    preview: str
    value_length: int


def _compile(pattern: str, flags: int = 0) -> re.Pattern[str]:
    return re.compile(pattern, flags)


_SUFFIX = r"(?![A-Za-z0-9_-])"

SECRET_RULES: Final[tuple[SecretRule, ...]] = (
    SecretRule(
        "aws-access-key",
        "AWS access key id",
        _compile(r"\b((?:AKIA|ASIA|ABIA|ACCA)[A-Z0-9]{16})\b"),
        SecretSeverity.HIGH,
    ),
    SecretRule(
        "aws-secret-key",
        "AWS secret access key assignment",
        _compile(
            r"(?i)aws[_\-]?secret[_\-]?access[_\-]?key\s*[:=]\s*[\"']?([A-Za-z0-9/+=]{40})"
            + _SUFFIX
        ),
        SecretSeverity.CRITICAL,
    ),
    SecretRule(
        "github-token",
        "GitHub token",
        _compile(r"\b(gh[pousr]_[A-Za-z0-9]{36,255})\b"),
        SecretSeverity.CRITICAL,
    ),
    SecretRule(
        "github-fine-grained-token",
        "GitHub fine-grained personal access token",
        _compile(r"\b(github_pat_[A-Za-z0-9_]{22,255})\b"),
        SecretSeverity.CRITICAL,
    ),
    SecretRule(
        "anthropic-key",
        "Anthropic API key",
        _compile(r"\b(sk-ant-[A-Za-z0-9_-]{20,})" + _SUFFIX),
        SecretSeverity.CRITICAL,
    ),
    SecretRule(
        "openai-key",
        "OpenAI-style API key",
        _compile(r"\b(sk-[A-Za-z0-9_-]{20,})" + _SUFFIX),
        SecretSeverity.CRITICAL,
    ),
    SecretRule(
        "openrouter-key",
        "OpenRouter API key",
        _compile(r"\b(sk-or-v1-[A-Za-z0-9]{32,})" + _SUFFIX),
        SecretSeverity.CRITICAL,
    ),
    SecretRule(
        "slack-token",
        "Slack token",
        _compile(r"\b(xox[baprs]-[A-Za-z0-9-]{10,})\b"),
        SecretSeverity.HIGH,
    ),
    SecretRule(
        "stripe-key",
        "Stripe API key",
        _compile(r"\b((?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,})" + _SUFFIX),
        SecretSeverity.CRITICAL,
    ),
    SecretRule(
        "google-api-key",
        "Google API key",
        _compile(r"\b(AIza[0-9A-Za-z_-]{35})\b"),
        SecretSeverity.HIGH,
    ),
    SecretRule(
        "sendgrid-key",
        "SendGrid API key",
        _compile(r"\b(SG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,})\b"),
        SecretSeverity.HIGH,
    ),
    SecretRule(
        "private-key-block",
        "Private key material",
        _compile(r"(-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----)"),
        SecretSeverity.CRITICAL,
    ),
    SecretRule(
        "jwt",
        "JSON Web Token",
        _compile(r"\b(eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})\b"),
        SecretSeverity.MEDIUM,
    ),
    SecretRule(
        "database-url-with-password",
        "Database URL containing a password",
        _compile(
            r"(?i)\b((?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis|amqp|mssql)"
            r"://[^:@/\s]+:([^@/\s]{4,})@)"
        ),
        SecretSeverity.CRITICAL,
        group=0,
    ),
    SecretRule(
        "generic-secret-assignment",
        "Possible hardcoded credential",
        _compile(
            r"""(?ix)
            \b(?:api[_-]?key|apikey|secret[_-]?key|client[_-]?secret|auth[_-]?token
               |access[_-]?token|refresh[_-]?token|password|passwd|pwd)
            \s*[:=]\s*
            ["']?
            ([A-Za-z0-9+/=_\-\.!@#$%^&*]{8,120})
            """
            + _SUFFIX
        ),
        SecretSeverity.HIGH,
    ),
)

_PLACEHOLDER_EXACT: Final[frozenset[str]] = frozenset(
    {
        "changeme",
        "change_me",
        "change-me",
        "password",
        "secret",
        "none",
        "null",
        "nil",
        "test",
        "fake",
        "dummy",
        "example",
        "todo",
        "redacted",
        "placeholder",
        "your_key_here",
        "your-key-here",
        "your_token_here",
        "your-token-here",
        "insert_key_here",
        "xxx",
        "xxxx",
        "...",
        "***",
    }
)

_PLACEHOLDER_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"^<.*>$"),
    re.compile(r"^\$\{.*\}$"),
    re.compile(r"^\$\(.*\)$"),
    re.compile(r"^\{\{.*\}\}$"),
    re.compile(r"(?i)^your[_-]"),
    re.compile(r"(?i)[_-]here$"),
    re.compile(r"(?i)^change[_-]?me"),
    re.compile(r"(?i)^replace[_-]?me"),
    re.compile(r"(?i)^(?:insert|paste|add)[_-]"),
    re.compile(r"^x{3,}$"),
    re.compile(r"^\*+$"),
    re.compile(r"^\.+$"),
)


def looks_like_placeholder(value: str) -> bool:
    """Return ``True`` for templated or obviously fake secret values.

    The check is deliberately conservative: a real-looking value such as
    ``supersecret`` is *not* a placeholder, while ``${API_KEY}``,
    ``<your-key>`` and ``changeme`` are.
    """
    candidate = value.strip().strip("\"'")
    if len(candidate) < 8:
        return True
    lowered = candidate.lower()
    if lowered in _PLACEHOLDER_EXACT:
        return True
    if any(pattern.match(candidate) for pattern in _PLACEHOLDER_PATTERNS):
        return True
    if len(set(candidate)) <= 2:  # e.g. "aaaaaaaa", "abababab"
        return True
    return candidate.replace("*", "").strip() == ""


_LOCAL_DB_HOSTS: Final[tuple[str, ...]] = (
    "localhost",
    "127.0.0.1",
    "::1",
    "db",
    "database",
    "postgres",
    "mysql",
    "mongo",
    "redis",
)
_LOCAL_DB_PASSWORDS: Final[frozenset[str]] = frozenset(
    {
        "postgres",
        "password",
        "root",
        "admin",
        "dev",
        "local",
        "test",
        "example",
        "changeme",
        "app",
        "user",
        "demo",
    }
)


def is_local_dev_database_url(value: str) -> bool:
    """True for throwaway local development connection strings.

    ``postgresql://postgres:postgres@localhost:5432/app`` in an ``.env.example``
    is a documentation default, not a leaked credential. A well-known default
    password with no visible host (the regex only captures up to ``@``) is also
    treated as a development default.
    """
    match = re.search(r"(?i)://[^:@/\s]+:(?P<password>[^@/\s]+)@(?P<host>[^:/\s]+)?", value)
    if match is None:
        return False
    password = match.group("password").lower()
    if password not in _LOCAL_DB_PASSWORDS:
        return False
    host = (match.group("host") or "").lower()
    if not host:
        return True
    return host in _LOCAL_DB_HOSTS or host.startswith(
        ("localhost", "127.", "192.168.", "10.", "172.")
    )


def _preview(value: str) -> str:
    """Return a masked preview that never reveals the whole secret."""
    value = value.strip()
    if len(value) <= 8:
        return REDACTED
    return f"{value[:3]}…{value[-2:]} ({len(value)} chars)"


def find_secrets(text: str, *, max_findings: int = 200) -> list[SecretFinding]:
    """Find secrets in ``text`` without returning their values."""
    findings: list[SecretFinding] = []
    index = LineIndex(text)
    for rule in SECRET_RULES:
        for match in rule.pattern.finditer(text):
            try:
                value = match.group(rule.group)
            except IndexError:  # pragma: no cover - rule misconfiguration
                continue
            if value is None:
                continue
            if looks_like_placeholder(value):
                continue
            if rule.id == "database-url-with-password" and is_local_dev_database_url(value):
                continue
            findings.append(
                SecretFinding(
                    rule_id=rule.id,
                    description=rule.description,
                    severity=rule.severity,
                    line=index.line_of(match.start(rule.group)),
                    preview=_preview(value),
                    value_length=len(value),
                )
            )
            if len(findings) >= max_findings:
                return findings
    findings.sort(key=lambda item: (item.line, item.rule_id))
    return findings


# Filenames whose contents must never be read or copied into artefacts.
SECRET_FILE_NAMES: Final[frozenset[str]] = frozenset(
    {
        ".env",
        ".npmrc",
        ".pypirc",
        ".netrc",
        "_netrc",
        ".htpasswd",
        "credentials",
        "credentials.json",
        "secrets.json",
        "secrets.yaml",
        "secrets.yml",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        ".git-credentials",
    }
)

SECRET_FILE_SUFFIXES: Final[tuple[str, ...]] = (
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".jks",
    ".keystore",
    ".ppk",
)

#: Files that look secret-ish but are explicitly meant to be committed.
SAFE_TEMPLATE_SUFFIXES: Final[tuple[str, ...]] = (
    ".example",
    ".sample",
    ".template",
    ".dist",
    ".example.local",
)
