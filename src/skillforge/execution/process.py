"""Bounded, orphan-free process management for controlled execution.

Long-running application processes are started in their own process group (or
Windows process tree) and terminated as a group, so grandchildren such as
``npm -> node`` or ``make -> python`` cannot survive a failed learning session.
Stdout/stderr are drained by reader threads into bounded buffers, which prevents
pipe deadlocks and unbounded memory use.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import IO

from skillforge.models.learning import DEFAULT_OUTPUT_LIMIT_BYTES

_READ_CHUNK = 4096
_JOIN_TIMEOUT = 2.0


class _BoundedBuffer:
    """Thread-safe byte buffer that keeps at most ``limit`` bytes."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._chunks: list[bytes] = []
        self._size = 0
        self._truncated = False
        self._lock = threading.Lock()

    def append(self, chunk: bytes) -> None:
        if not chunk:
            return
        with self._lock:
            if self._size >= self._limit:
                self._truncated = True
                return
            remaining = self._limit - self._size
            if len(chunk) > remaining:
                chunk = chunk[:remaining]
                self._truncated = True
            self._chunks.append(chunk)
            self._size += len(chunk)

    @property
    def truncated(self) -> bool:
        with self._lock:
            return self._truncated

    def text(self) -> str:
        with self._lock:
            raw = b"".join(self._chunks)
        return raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")


class ManagedProcess:
    """A started subprocess with bounded logs and group termination."""

    def __init__(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        output_limit: int = DEFAULT_OUTPUT_LIMIT_BYTES,
    ) -> None:
        self.argv = argv
        self.cwd = cwd
        self._stdout = _BoundedBuffer(output_limit)
        self._stderr = _BoundedBuffer(output_limit)
        self._started_at = time.monotonic()
        if os.name == "nt":  # pragma: no cover - platform specific
            self._process = subprocess.Popen(
                list(argv),
                cwd=str(cwd),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        else:
            self._process = subprocess.Popen(
                list(argv),
                cwd=str(cwd),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                start_new_session=True,
            )
        self._threads = [
            threading.Thread(
                target=self._drain, args=(self._process.stdout, self._stdout), daemon=True
            ),
            threading.Thread(
                target=self._drain, args=(self._process.stderr, self._stderr), daemon=True
            ),
        ]
        for thread in self._threads:
            thread.start()

    # ------------------------------------------------------------------ state
    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def returncode(self) -> int | None:
        return self._process.poll()

    def is_running(self) -> bool:
        return self._process.poll() is None

    @property
    def elapsed_ms(self) -> float:
        return (time.monotonic() - self._started_at) * 1000

    @property
    def truncated(self) -> bool:
        return self._stdout.truncated or self._stderr.truncated

    def wait(self, timeout: float) -> int | None:
        """Wait up to ``timeout`` seconds; ``None`` when still running."""
        try:
            return self._process.wait(timeout=max(0.0, timeout))
        except subprocess.TimeoutExpired:
            return None

    # ------------------------------------------------------------------- logs
    def _join_readers(self, timeout: float = _JOIN_TIMEOUT) -> None:
        for thread in self._threads:
            thread.join(timeout=timeout)

    def stdout_text(self) -> str:
        """Text captured so far; waits briefly for the reader to catch up."""
        self._join_readers(0.5)
        return self._stdout.text()

    def stderr_text(self) -> str:
        """Text captured so far; waits briefly for the reader to catch up."""
        self._join_readers(0.5)
        return self._stderr.text()

    def logs_text(self) -> str:
        """Text captured so far, without waiting (readiness polls this often)."""
        parts = [self._stdout.text(), self._stderr.text()]
        return "\n".join(part for part in parts if part)

    @staticmethod
    def _drain(stream: IO[bytes] | None, buffer: _BoundedBuffer) -> None:
        if stream is None:  # pragma: no cover - defensive
            return
        # ``read1`` returns as soon as any data is available; ``read(n)`` would
        # block until the buffer fills, so a long-running app that prints two
        # lines and keeps running would appear to have produced no logs at all.
        reader = getattr(stream, "read1", None) or stream.read
        try:
            while True:
                chunk = reader(_READ_CHUNK)
                if not chunk:
                    return
                buffer.append(chunk)
        except (OSError, ValueError):  # pragma: no cover - stream closed during terminate
            return

    # ------------------------------------------------------------ termination
    def terminate(self, *, grace: float = 3.0) -> bool:
        """Terminate the process tree. Returns True when it is no longer running."""
        if self._process.poll() is not None:
            self._join_readers(0.5)
            return True

        if os.name == "nt":  # pragma: no cover - platform specific
            self._terminate_windows()
        else:
            self._terminate_posix(grace)

        try:
            self._process.wait(timeout=max(grace, 1.0))
        except subprocess.TimeoutExpired:  # pragma: no cover - stubborn process
            self._process.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                self._process.wait(timeout=5.0)
        self._join_readers(1.0)
        return self._process.poll() is not None

    def _terminate_posix(self, grace: float) -> None:
        getpgid = getattr(os, "getpgid", None)
        killpg = getattr(os, "killpg", None)
        sigkill = getattr(signal, "SIGKILL", signal.SIGTERM)
        pgid: int | None = None
        if getpgid is not None:
            try:
                pgid = getpgid(self.pid)
            except (ProcessLookupError, PermissionError):  # pragma: no cover - race
                pgid = None
        try:
            if pgid is not None and killpg is not None:
                killpg(pgid, signal.SIGTERM)
            else:
                self._process.terminate()
        except (ProcessLookupError, PermissionError):  # pragma: no cover - race
            return
        try:
            self._process.wait(timeout=max(grace, 0.5))
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            if pgid is not None and killpg is not None:
                killpg(pgid, sigkill)
            else:
                self._process.kill()
        except (ProcessLookupError, PermissionError):  # pragma: no cover - race
            return

    def _terminate_windows(self) -> None:  # pragma: no cover - platform specific
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                ["taskkill", "/PID", str(self.pid), "/T", "/F"],
                capture_output=True,
                timeout=10.0,
                check=False,
                shell=False,
            )
        with contextlib.suppress(OSError):
            self._process.kill()

    def __enter__(self) -> ManagedProcess:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.terminate()


__all__ = ["ManagedProcess"]
