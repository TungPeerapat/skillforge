"""Deterministic readiness strategies.

"Process started" is not "application works". Every readiness check is bounded,
polled, and evidence-linked:

* ``http``  — GET a loopback URL and expect 2xx;
* ``tcp``   — open a TCP connection to a loopback port;
* ``log_signal`` — wait for a known startup line in the captured logs;
* ``process`` — only check that the process is still alive after a grace period.
"""

from __future__ import annotations

import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime

from skillforge import __version__
from skillforge.execution.base import ManagedProcessLike
from skillforge.models.learning import (
    ReadinessConfidence,
    ReadinessKind,
    ReadinessPlan,
    VerificationResult,
)

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})
_PROBE_TIMEOUT = 2.0
_POLL_INTERVAL = 0.25


def is_loopback(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in ("http",) and (parsed.hostname or "").lower() in LOOPBACK_HOSTS


def _connect_host(host: str | None) -> str:
    if host in (None, "", "0.0.0.0", "::"):
        return "127.0.0.1"
    return host


def _http_probe(url: str, expect_status: str) -> tuple[bool, int | None, str]:
    request = urllib.request.Request(
        url, method="GET", headers={"User-Agent": f"skillforge/{__version__}"}
    )
    try:
        with urllib.request.urlopen(request, timeout=_PROBE_TIMEOUT) as response:
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        return False, status, f"GET {url} returned {status}"
    except urllib.error.URLError as exc:
        return False, None, f"GET {url} failed: {exc.reason}"
    except (ValueError, OSError) as exc:
        return False, None, f"GET {url} failed: {exc}"
    if expect_status.endswith("xx") and expect_status[0].isdigit():
        low = int(expect_status[0]) * 100
        success = low <= status <= low + 99
    else:
        success = str(status) == expect_status.strip()
    detail = f"GET {url} returned {status}"
    return success, status, detail


def _tcp_probe(host: str, port: int) -> tuple[bool, str]:
    try:
        with socket.create_connection((_connect_host(host), port), timeout=_PROBE_TIMEOUT):
            return True, f"TCP {host}:{port} accepted a connection"
    except OSError as exc:
        return False, f"TCP {host}:{port} failed: {exc}"


def _result(
    readiness: ReadinessPlan,
    *,
    success: bool,
    detail: str,
    started: float,
    checks: int,
    status_code: int | None = None,
) -> VerificationResult:
    return VerificationResult(
        success=success,
        kind=readiness.kind,
        confidence=readiness.confidence,
        target=readiness.target,
        status_code=status_code,
        detail=detail,
        observed_at=datetime.now(UTC),
        duration_ms=round((time.monotonic() - started) * 1000, 3),
        checks=checks,
        evidence=list(readiness.evidence),
    )


def evaluate(
    readiness: ReadinessPlan,
    process: ManagedProcessLike | None,
    *,
    timeout: float,
    poll_interval: float = _POLL_INTERVAL,
) -> VerificationResult:
    """Poll ``readiness`` until it succeeds or ``timeout`` seconds elapse."""
    started = time.monotonic()
    deadline = started + max(0.1, timeout)
    checks = 0
    last_detail = "not ready yet"
    last_status: int | None = None
    interval = max(0.05, poll_interval)

    while True:
        if process is not None and not process.is_running():
            exit_code = getattr(process, "returncode", None)
            return _result(
                readiness,
                success=False,
                detail=f"process exited before readiness (exit code {exit_code})",
                started=started,
                checks=checks,
            )
        checks += 1
        if readiness.kind is ReadinessKind.HTTP and readiness.url:
            if not is_loopback(readiness.url):
                return _result(
                    readiness,
                    success=False,
                    detail=(
                        f"refusing to probe non-loopback URL: {readiness.url} "
                        "(readiness checks never leave the machine)"
                    ),
                    started=started,
                    checks=checks,
                )
            success, status, detail = _http_probe(readiness.url, readiness.expect_status)
            if success:
                return _result(
                    readiness,
                    success=True,
                    detail=detail,
                    started=started,
                    checks=checks,
                    status_code=status,
                )
            last_detail = detail
            last_status = status
        elif readiness.kind is ReadinessKind.TCP and readiness.port is not None:
            success, detail = _tcp_probe(readiness.host or "127.0.0.1", readiness.port)
            if success:
                return _result(
                    readiness, success=True, detail=detail, started=started, checks=checks
                )
            last_detail = detail
        elif readiness.kind is ReadinessKind.LOG_SIGNAL and readiness.log_pattern:
            logs = process.logs_text() if process is not None else ""
            if logs and re.search(readiness.log_pattern, logs, re.IGNORECASE):
                return _result(
                    readiness,
                    success=True,
                    detail=f"log matched /{readiness.log_pattern}/",
                    started=started,
                    checks=checks,
                )
            last_detail = f"no log line matched /{readiness.log_pattern}/"
        elif readiness.kind is ReadinessKind.PROCESS:
            grace = max(0.0, readiness.grace_seconds)
            elapsed = time.monotonic() - started
            if elapsed >= grace and (process is None or process.is_running()):
                return _result(
                    readiness,
                    success=True,
                    detail="process stayed alive for the grace period",
                    started=started,
                    checks=checks,
                )
            last_detail = "waiting for the process to stay alive"
        else:  # pragma: no cover - plan builders always populate the fields
            return _result(
                readiness,
                success=False,
                detail="readiness plan is incomplete",
                started=started,
                checks=checks,
            )

        if time.monotonic() >= deadline:
            return _result(
                readiness,
                success=False,
                detail=f"timed out after {timeout:.1f}s: {last_detail}",
                started=started,
                checks=checks,
                status_code=last_status,
            )
        time.sleep(interval)


def process_only_plan(*, grace_seconds: float = 2.0, detail: str = "") -> ReadinessPlan:
    """Weakest fallback: only "the process is still alive" can be checked."""
    return ReadinessPlan(
        kind=ReadinessKind.PROCESS,
        confidence=ReadinessConfidence.PROCESS_ONLY,
        grace_seconds=grace_seconds,
        detail=detail or "no endpoint evidence; only process liveness can be verified",
    )


__all__ = ["LOOPBACK_HOSTS", "evaluate", "is_loopback", "process_only_plan"]
