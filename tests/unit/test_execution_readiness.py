"""Unit tests for deterministic readiness strategies."""

from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from skillforge.execution.readiness import evaluate, is_loopback, process_only_plan
from skillforge.models import ReadinessConfidence, ReadinessKind, ReadinessPlan

pytestmark = pytest.mark.execution


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        code = 200 if self.path == "/health" else 500
        self.send_response(code)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, format: str, *args: object) -> None:
        return


class _FakeProcess:
    """Minimal ManagedProcessLike for log/process readiness tests."""

    def __init__(self, *, alive: bool = True, logs: str = "") -> None:
        self.pid = 1234
        self._alive = alive
        self._logs = logs

    @property
    def returncode(self) -> int | None:
        return None if self._alive else 1

    def is_running(self) -> bool:
        return self._alive

    def wait(self, timeout: float) -> int | None:
        return None if self._alive else 1

    def stdout_text(self) -> str:
        return self._logs

    def stderr_text(self) -> str:
        return ""

    def logs_text(self) -> str:
        return self._logs

    @property
    def truncated(self) -> bool:
        return False

    def terminate(self, *, grace: float = 3.0) -> bool:
        self._alive = False
        return True


@pytest.fixture
def http_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def test_http_readiness_success(http_server: str) -> None:
    plan = ReadinessPlan(
        kind=ReadinessKind.HTTP,
        confidence=ReadinessConfidence.EVIDENCED,
        url=f"{http_server}/health",
    )
    result = evaluate(plan, None, timeout=5)
    assert result.success
    assert result.status_code == 200
    assert result.checks >= 1


def test_http_non_2xx_fails(http_server: str) -> None:
    plan = ReadinessPlan(kind=ReadinessKind.HTTP, url=f"{http_server}/broken")
    result = evaluate(plan, None, timeout=1)
    assert not result.success
    assert result.status_code == 500


def test_http_timeout_when_nothing_listens() -> None:
    plan = ReadinessPlan(kind=ReadinessKind.HTTP, url="http://127.0.0.1:9/health")
    result = evaluate(plan, None, timeout=1, poll_interval=0.05)
    assert not result.success
    assert "timed out" in result.detail


def test_http_refuses_non_loopback() -> None:
    plan = ReadinessPlan(kind=ReadinessKind.HTTP, url="http://example.com/health")
    result = evaluate(plan, None, timeout=1)
    assert not result.success
    assert "refusing to probe non-loopback" in result.detail
    assert not is_loopback("http://example.com/health")
    assert is_loopback("http://localhost:8000/health")


def test_http_fails_fast_when_process_exits() -> None:
    plan = ReadinessPlan(kind=ReadinessKind.HTTP, url="http://127.0.0.1:9/health")
    process = _FakeProcess(alive=False)
    result = evaluate(plan, process, timeout=5)
    assert not result.success
    assert "process exited" in result.detail


def test_tcp_readiness_success(http_server: str) -> None:
    port = int(http_server.rsplit(":", 1)[1])
    plan = ReadinessPlan(kind=ReadinessKind.TCP, host="127.0.0.1", port=port)
    result = evaluate(plan, None, timeout=5)
    assert result.success


def test_tcp_readiness_failure() -> None:
    plan = ReadinessPlan(kind=ReadinessKind.TCP, host="127.0.0.1", port=9)
    result = evaluate(plan, None, timeout=1, poll_interval=0.05)
    assert not result.success


def test_log_signal_readiness() -> None:
    plan = ReadinessPlan(kind=ReadinessKind.LOG_SIGNAL, log_pattern=r"listening on")
    process = _FakeProcess(logs="booting\nlistening on http://127.0.0.1:8123\n")
    result = evaluate(plan, process, timeout=1)
    assert result.success


def test_log_signal_times_out() -> None:
    plan = ReadinessPlan(kind=ReadinessKind.LOG_SIGNAL, log_pattern=r"listening on")
    process = _FakeProcess(logs="booting\n")
    result = evaluate(plan, process, timeout=1, poll_interval=0.05)
    assert not result.success


def test_process_only_readiness() -> None:
    plan = process_only_plan(grace_seconds=0.1)
    start = time.monotonic()
    result = evaluate(plan, _FakeProcess(), timeout=5)
    assert result.success
    assert time.monotonic() - start >= 0.1
    assert result.confidence is ReadinessConfidence.PROCESS_ONLY


def test_process_only_fails_when_process_dies() -> None:
    plan = process_only_plan(grace_seconds=1.0)
    result = evaluate(plan, _FakeProcess(alive=False), timeout=5)
    assert not result.success
