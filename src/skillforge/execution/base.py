"""Execution backend abstraction.

Every controlled execution in SkillForge goes through an
:class:`ExecutionBackend`. Nothing else in the codebase may spawn a repository
process. v0.2 ships :class:`~skillforge.execution.local.LocalExecutionBackend`;
a container backend can be added later without touching the learning engine.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from skillforge.models.learning import (
    DEFAULT_OUTPUT_LIMIT_BYTES,
    ReadinessPlan,
    VerificationResult,
)


@dataclass(frozen=True)
class ExecutionRequest:
    """A bounded, argv-only execution request."""

    argv: tuple[str, ...]
    cwd: Path
    timeout: float = 120.0
    output_limit: int = DEFAULT_OUTPUT_LIMIT_BYTES
    #: Human-readable label used in logs (never sent anywhere).
    label: str = ""


@dataclass
class ExecutionOutcome:
    """Result of a one-shot command."""

    argv: tuple[str, ...]
    cwd: str
    exit_code: int | None = None
    timed_out: bool = False
    duration_ms: float = 0.0
    stdout: str = ""
    stderr: str = ""
    truncated: bool = False
    #: Set when execution was refused before any process was spawned.
    blocked_reason: str | None = None
    #: Set when the process could not be started (missing executable, …).
    start_error: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (
            self.blocked_reason is None
            and self.start_error is None
            and not self.timed_out
            and self.exit_code == 0
        )

    @property
    def status_detail(self) -> str:
        if self.blocked_reason:
            return f"blocked: {self.blocked_reason}"
        if self.start_error:
            return f"could not start: {self.start_error}"
        if self.timed_out:
            return "timed out"
        return f"exit code {self.exit_code}"


class ExecutionBackend(ABC):
    """The narrow contract between the learning engine and the operating system."""

    #: Stable identifier shown in plans and session records.
    id: str = ""

    @abstractmethod
    def run(self, request: ExecutionRequest) -> ExecutionOutcome:
        """Run a bounded command to completion (or timeout)."""

    @abstractmethod
    def start(self, request: ExecutionRequest) -> ManagedProcessLike:
        """Start a long-running command and return a managed handle."""

    @abstractmethod
    def verify(
        self,
        readiness: ReadinessPlan,
        process: ManagedProcessLike | None,
        *,
        timeout: float,
    ) -> VerificationResult:
        """Evaluate a readiness plan against a running process."""

    @abstractmethod
    def cleanup(self) -> None:
        """Terminate everything this backend started, without raising."""

    @abstractmethod
    def describe(self) -> str:
        """One-line description of what this backend does and does not isolate."""


@runtime_checkable
class ManagedProcessLike(Protocol):
    """Structural interface implemented by :class:`ManagedProcess`.

    Backends return handles that satisfy this protocol, which keeps the session
    engine independent of the concrete process implementation.
    """

    @property
    def pid(self) -> int: ...

    @property
    def returncode(self) -> int | None: ...

    def is_running(self) -> bool: ...

    def wait(self, timeout: float) -> int | None: ...

    def stdout_text(self) -> str: ...

    def stderr_text(self) -> str: ...

    def logs_text(self) -> str: ...

    @property
    def truncated(self) -> bool: ...

    def terminate(self, *, grace: float = 3.0) -> bool: ...


__all__ = [
    "ExecutionBackend",
    "ExecutionOutcome",
    "ExecutionRequest",
    "ManagedProcessLike",
]
