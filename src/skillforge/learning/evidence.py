"""Readiness evidence extraction.

Readiness must be grounded in repository evidence. This module reads only the
content the scanner already retained (secrets excluded, snippets redacted) and
derives the strongest deterministic readiness strategy it can justify:

1. an explicit loopback URL or health path documented for the project
   (``evidenced``);
2. a port derived from command flags, compose mappings, env templates, or a
   framework default (``inferred``);
3. a known startup log signal (``inferred``);
4. process liveness only (``process_only``).

``/health`` is never invented: if the repository does not mention a health
endpoint, no HTTP readiness check is produced.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

from skillforge.models import (
    Command,
    Evidence,
    ReadinessConfidence,
    ReadinessKind,
    ReadinessPlan,
    RepositoryProfile,
    Service,
)
from skillforge.models.workflow import ServiceKind

#: Health-ish paths that are worth probing even without an explicit URL line.
HEALTH_PATHS: tuple[str, ...] = (
    "/health",
    "/healthz",
    "/health/live",
    "/health/ready",
    "/ready",
    "/readyz",
    "/live",
    "/livez",
    "/ping",
    "/status",
    "/up",
    "/actuator/health",
)

#: Lines that demonstrate an endpoint, e.g. ``curl http://localhost:8000/health``.
_ENDPOINT_CONTEXT_RE = re.compile(
    r"(?i)\b(curl|wget|https?|fetch|probe|smoke|healthcheck|readiness|liveness|healthz?)\b"
)
#: Route definitions are excellent endpoint evidence, e.g. ``app.get("/health")``.
_ROUTE_CONTEXT_RE = re.compile(r"\.\s*(?:get|route|add_api_route)\s*\(\s*['\"]")


def _has_endpoint_context(line: str) -> bool:
    return bool(_ENDPOINT_CONTEXT_RE.search(line) or _ROUTE_CONTEXT_RE.search(line))


_URL_RE = re.compile(
    r"https?://(?:localhost|127\.0\.0\.1)(?::(?P<port>\d{2,5}))?(?P<path>/[A-Za-z0-9._~\-/]*)?",
    re.IGNORECASE,
)
_BARE_PATH_RE = re.compile(
    r"(?i)(?:get|curl|wget|probe|check)?\s*(?P<path>/(?:healthz?|readyz?|livez?|ping|status|up))\b"
)
_PORT_FLAG_RE = re.compile(r"--port[= ](?P<port>\d{2,5})\b")
_PORT_ENV_RE = re.compile(r"(?im)^\s*(?:export\s+)?PORT\s*=\s*(?P<port>\d{2,5})\s*$")
_LOG_SIGNALS: tuple[str, ...] = (
    r"application startup complete",
    r"uvicorn running on",
    r"running on http",
    r"now listening on",
    r"server listening",
    r"server started",
    r"listening (?:at|on)",
    r"ready in \d+",
    r"waiting for application startup",
)

#: Framework name (lowercase) -> conventional local port.
_FRAMEWORK_PORTS: tuple[tuple[tuple[str, ...], int], ...] = (
    (("uvicorn", "gunicorn", "flask", "fastapi", "falcon"), 8000),
    (("next.js", "next", "vite", "nuxt", "remix", "astro", "react"), 3000),
    (("django",), 8000),
    (("express", "fastify", "nestjs", "node.js"), 3000),
    (("go", "gin", "echo", "fiber", "chi"), 8080),
    (("asp.net", "asp net", ".net"), 5000),
    (("spring boot", "spring"), 8080),
    (("rails",), 3000),
)

_APPLICATION_KINDS = frozenset(
    {ServiceKind.WEB, ServiceKind.PROXY, ServiceKind.WORKER, ServiceKind.OTHER}
)


@dataclass(frozen=True)
class EndpointEvidence:
    """A loopback endpoint found in retained repository content."""

    url: str
    path: str
    port: int
    source: str
    line: str


def _iter_content(scan_contents: dict[str, str]) -> Iterator[tuple[str, int, str]]:
    for path in sorted(scan_contents):
        text = scan_contents[path]
        if not text:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            yield path, number, line


def find_documented_endpoints(scan_contents: dict[str, str]) -> list[EndpointEvidence]:
    """Loopback URLs in documentation/configuration, most health-like first."""
    found: list[EndpointEvidence] = []
    seen: set[str] = set()
    for path, number, line in _iter_content(scan_contents):
        if not _has_endpoint_context(line):
            continue
        for match in _URL_RE.finditer(line):
            candidate_path = match.group("path") or ""
            port = int(match.group("port")) if match.group("port") else _default_port_for(line)
            if port is None:
                continue
            if not candidate_path:
                continue
            url = f"http://127.0.0.1:{port}{candidate_path.rstrip('/')}"
            if url in seen:
                continue
            seen.add(url)
            found.append(
                EndpointEvidence(
                    url=url,
                    path=candidate_path,
                    port=port,
                    source=f"{path}:L{number}",
                    line=line.strip()[:200],
                )
            )

    def rank(endpoint: EndpointEvidence) -> tuple[int, str]:
        lowered = endpoint.path.lower()
        try:
            health_rank = HEALTH_PATHS.index(next(p for p in HEALTH_PATHS if lowered == p))
        except StopIteration:
            health_rank = len(HEALTH_PATHS)
        return (health_rank, endpoint.source)

    found.sort(key=rank)
    return found


def _default_port_for(line: str) -> int | None:
    flag = _PORT_FLAG_RE.search(line)
    if flag:
        return int(flag.group("port"))
    for match in _URL_RE.finditer(line):
        if match.group("port"):
            return int(match.group("port"))
    return None


def find_bare_health_path(scan_contents: dict[str, str]) -> tuple[str, str] | None:
    """A health path mentioned without a full URL, e.g. ``GET /health``."""
    for path, number, line in _iter_content(scan_contents):
        if not _has_endpoint_context(line):
            continue
        match = _BARE_PATH_RE.search(line)
        if match:
            candidate = match.group("path").lower()
            if candidate in HEALTH_PATHS:
                return candidate, f"{path}:L{number}"
    return None


def find_port(
    profile: RepositoryProfile,
    scan_contents: dict[str, str],
    commands: list[Command],
) -> tuple[int, str] | None:
    """Best evidence-derived local port: flags, env templates, compose, defaults."""
    for command in commands:
        match = _PORT_FLAG_RE.search(command.command)
        if match:
            return int(match.group("port")), command.provenance
    for env_file in sorted(set(profile.environment_files)):
        text = scan_contents.get(env_file, "")
        match = _PORT_ENV_RE.search(text)
        if match:
            return int(match.group("port")), env_file
    for service in profile.services:
        if service.kind not in _APPLICATION_KINDS:
            continue
        port = _host_port(service)
        if port is not None:
            label = service.evidence[0].label if service.evidence else service.name
            return port, label
    lowered = (
        " ".join(tech.name.lower() for tech in profile.technologies)
        + " "
        + " ".join(command.command.lower() for command in commands)
    )
    for names, port in _FRAMEWORK_PORTS:
        if any(name in lowered for name in names):
            return port, "framework default"
    return None


def _host_port(service: Service) -> int | None:
    for mapping in service.ports:
        host_part = mapping.split(":")[0] if ":" in mapping else mapping
        host_part = host_part.split("/")[0]
        if host_part.isdigit():
            port = int(host_part)
            if 1 <= port <= 65535:
                return port
    return None


def known_log_pattern() -> str:
    return "|".join(f"(?:{signal})" for signal in _LOG_SIGNALS)


#: Paths that are meaningful readiness probes. A documented endpoint such as
#: ``/api/items`` is evidence for a port, not for "the app is healthy".
_READINESS_PATHS: frozenset[str] = frozenset(HEALTH_PATHS) | {"/"}


def is_readiness_path(path: str | None) -> bool:
    if not path:
        return False
    cleaned = path.split("?", 1)[0].split("#", 1)[0].lower()
    if len(cleaned) > 1:
        cleaned = cleaned.rstrip("/")
    return cleaned in _READINESS_PATHS


def build_readiness(
    profile: RepositoryProfile,
    scan_contents: dict[str, str],
    commands: list[Command],
    *,
    confidence_floor: ReadinessConfidence = ReadinessConfidence.INFERRED,
) -> ReadinessPlan:
    """Deterministically choose the strongest readiness strategy available."""
    del confidence_floor  # reserved for future per-target policy
    endpoints = find_documented_endpoints(scan_contents)
    readiness_endpoints = [item for item in endpoints if is_readiness_path(item.path)]
    if readiness_endpoints:
        endpoint = readiness_endpoints[0]
        return ReadinessPlan(
            kind=ReadinessKind.HTTP,
            confidence=ReadinessConfidence.EVIDENCED,
            url=endpoint.url,
            host="127.0.0.1",
            port=endpoint.port,
            path=endpoint.path,
            evidence=[
                Evidence(
                    source=endpoint.source.split(":L")[0],
                    locator=f"L{endpoint.source.split(':L')[-1]}",
                    detail="documented endpoint",
                    snippet=endpoint.line,
                    weight=0.8,
                )
            ],
            detail=f"documented endpoint {endpoint.url}",
        )

    bare = find_bare_health_path(scan_contents)
    port_info = find_port(profile, scan_contents, commands)
    if bare is not None and port_info is not None:
        health_path, source = bare
        port, port_source = port_info
        return ReadinessPlan(
            kind=ReadinessKind.HTTP,
            confidence=ReadinessConfidence.INFERRED,
            url=f"http://127.0.0.1:{port}{health_path}",
            host="127.0.0.1",
            port=port,
            path=health_path,
            evidence=[
                Evidence(
                    source=source.split(":L")[0],
                    locator=f"L{source.split(':L')[-1]}",
                    detail="health path documented",
                    weight=0.6,
                ),
                Evidence(source=port_source, detail="local port", weight=0.5),
            ],
            detail=f"health path from {source}; port from {port_source}",
        )

    if port_info is not None:
        port, port_source = port_info
        return ReadinessPlan(
            kind=ReadinessKind.TCP,
            confidence=ReadinessConfidence.INFERRED,
            host="127.0.0.1",
            port=port,
            evidence=[Evidence(source=port_source, detail="local port", weight=0.5)],
            detail=f"port from {port_source}; no health endpoint evidence",
        )

    if endpoints:
        # A documented non-readiness URL (for example a curl of /api/items)
        # proves a port, not that the application is healthy.
        endpoint = endpoints[0]
        return ReadinessPlan(
            kind=ReadinessKind.TCP,
            confidence=ReadinessConfidence.INFERRED,
            host="127.0.0.1",
            port=endpoint.port,
            evidence=[
                Evidence(
                    source=endpoint.source.split(":L")[0],
                    locator=f"L{endpoint.source.split(':L')[-1]}",
                    detail="documented endpoint is not a readiness probe; checking the port",
                    snippet=endpoint.line,
                    weight=0.5,
                )
            ],
            detail=(
                f"documented endpoint {endpoint.url} is not a readiness probe; "
                "checking the port instead"
            ),
        )

    return ReadinessPlan(
        kind=ReadinessKind.LOG_SIGNAL,
        confidence=ReadinessConfidence.INFERRED,
        log_pattern=known_log_pattern(),
        detail="no endpoint evidence; waiting for a known startup log line",
    )


__all__ = [
    "HEALTH_PATHS",
    "EndpointEvidence",
    "build_readiness",
    "find_bare_health_path",
    "find_documented_endpoints",
    "find_port",
    "is_readiness_path",
    "known_log_pattern",
]
