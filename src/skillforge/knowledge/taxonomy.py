"""Curated capability taxonomy and identifier tokenization (K5).

Plain data only: domains are curated keyword sets, detection is deterministic,
and nothing here reads the network or the filesystem.
"""

from __future__ import annotations

import re

from skillforge.models import CapabilityDomain, CapabilityKind

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_QUALIFIED_SPLIT = re.compile(r"[^A-Za-z0-9]+")
_TOKEN_MIN = 2
_SEARCH_TOKEN_MIN = 3

#: Tokens too generic to be useful postings keys.
STOPWORDS: frozenset[str] = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "this",
        "that",
        "are",
        "not",
        "use",
        "using",
        "used",
        "into",
        "when",
        "will",
        "return",
        "returns",
        "value",
        "values",
        "args",
        "kwargs",
    }
)

DEFAULT_DOMAINS: tuple[CapabilityDomain, ...] = (
    CapabilityDomain(
        id="http",
        name="HTTP & networking",
        description="Requests, responses, routing, and network clients/servers.",
        keywords=(
            "http",
            "https",
            "request",
            "response",
            "route",
            "router",
            "endpoint",
            "client",
            "server",
            "url",
            "uri",
            "fetch",
            "websocket",
            "socket",
        ),
    ),
    CapabilityDomain(
        id="serialization",
        name="Serialization & schemas",
        description="JSON/YAML/TOML encoding, schemas, and data models.",
        keywords=(
            "json",
            "jsonable",
            "serialize",
            "deserialize",
            "schema",
            "encoder",
            "decoder",
            "yaml",
            "toml",
            "dump",
            "loads",
            "dumps",
            "parse",
            "validate",
        ),
    ),
    CapabilityDomain(
        id="cli",
        name="CLI & commands",
        description="Command-line entry points, arguments, and flags.",
        keywords=("cli", "command", "argument", "option", "flag", "console", "script", "main"),
    ),
    CapabilityDomain(
        id="configuration",
        name="Configuration",
        description="Settings, environment variables, and runtime options.",
        keywords=("config", "setting", "settings", "environment", "env", "secret", "options"),
    ),
    CapabilityDomain(
        id="testing",
        name="Testing",
        description="Test helpers, fixtures, and assertions.",
        keywords=("test", "tests", "testing", "fixture", "mock", "assert", "pytest"),
    ),
    CapabilityDomain(
        id="database",
        name="Database & persistence",
        description="Engines, sessions, queries, migrations, and repositories.",
        keywords=(
            "database",
            "db",
            "sql",
            "query",
            "session",
            "engine",
            "migration",
            "repository",
            "transaction",
            "pool",
        ),
    ),
    CapabilityDomain(
        id="security",
        name="Security & auth",
        description="Authentication, tokens, permissions, and cryptography.",
        keywords=(
            "auth",
            "oauth",
            "token",
            "password",
            "security",
            "secure",
            "bearer",
            "permission",
            "crypto",
            "hash",
            "sign",
            "jwt",
        ),
    ),
    CapabilityDomain(
        id="async",
        name="Async & concurrency",
        description="Coroutines, tasks, backgrounds, and concurrency primitives.",
        keywords=(
            "async",
            "await",
            "concurrency",
            "task",
            "tasks",
            "future",
            "coroutine",
            "background",
        ),
    ),
    CapabilityDomain(
        id="observability",
        name="Observability",
        description="Logging, metrics, tracing, and telemetry.",
        keywords=("log", "logger", "logging", "metric", "trace", "span", "telemetry", "monitor"),
    ),
    CapabilityDomain(
        id="middleware",
        name="Middleware & events",
        description="Middleware chains, hooks, plugins, and event dispatch.",
        keywords=(
            "middleware",
            "plugin",
            "hook",
            "hooks",
            "event",
            "events",
            "listener",
            "dispatch",
        ),
    ),
)

_KIND_DOMAINS: dict[CapabilityKind, str] = {
    CapabilityKind.CLI_COMMAND: "cli",
    CapabilityKind.CONFIG_OPTION: "configuration",
    CapabilityKind.MIDDLEWARE: "middleware",
    CapabilityKind.HOOK: "middleware",
    CapabilityKind.EVENT: "middleware",
}


def domains() -> tuple[CapabilityDomain, ...]:
    return DEFAULT_DOMAINS


def domain_ids() -> tuple[str, ...]:
    return tuple(domain.id for domain in DEFAULT_DOMAINS)


def domain_for(domain_id: str) -> CapabilityDomain | None:
    for domain in DEFAULT_DOMAINS:
        if domain.id == domain_id:
            return domain
    return None


def split_identifier(text: str) -> list[str]:
    """Split identifiers on camelCase/snake/kebab/qualified boundaries."""
    expanded = _CAMEL_BOUNDARY.sub(" ", text)
    tokens = [
        token.lower() for token in _QUALIFIED_SPLIT.split(expanded) if len(token) >= _TOKEN_MIN
    ]
    return tokens


def tokenize_for_search(text: str) -> list[str]:
    """Search tokens: identifier split, lowercased, stop-worded, deduplicated."""
    seen: dict[str, None] = {}
    for token in split_identifier(text):
        if len(token) >= _SEARCH_TOKEN_MIN and token not in STOPWORDS:
            seen.setdefault(token)
    return list(seen)


def detect_domains(
    name: str,
    qualified_name: str = "",
    kind: CapabilityKind | None = None,
    extra_text: str = "",
) -> list[str]:
    """Deterministic keyword-based domain detection (marked INFERENCE upstream)."""
    tokens = set(split_identifier(name))
    tokens.update(split_identifier(qualified_name))
    tokens.update(tokenize_for_search(extra_text))
    matched: list[str] = []
    for domain in DEFAULT_DOMAINS:
        if tokens.intersection(domain.keywords):
            matched.append(domain.id)
    if kind is not None:
        hint = _KIND_DOMAINS.get(kind)
        if hint and hint not in matched:
            matched.append(hint)
    return sorted(matched)


__all__ = [
    "DEFAULT_DOMAINS",
    "STOPWORDS",
    "detect_domains",
    "domain_for",
    "domain_ids",
    "domains",
    "split_identifier",
    "tokenize_for_search",
]
