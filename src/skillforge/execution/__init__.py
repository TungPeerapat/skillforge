"""Controlled execution backends.

Nothing outside this package may spawn repository processes. v0.2 ships a
local backend only; the abstraction exists so a container backend can be added
without touching the learning engine.
"""

from __future__ import annotations

from skillforge.execution.base import (
    ExecutionBackend,
    ExecutionOutcome,
    ExecutionRequest,
    ManagedProcessLike,
)
from skillforge.execution.local import LocalExecutionBackend
from skillforge.execution.process import ManagedProcess
from skillforge.execution.readiness import evaluate, is_loopback, process_only_plan

__all__ = [
    "ExecutionBackend",
    "ExecutionOutcome",
    "ExecutionRequest",
    "LocalExecutionBackend",
    "ManagedProcess",
    "ManagedProcessLike",
    "evaluate",
    "is_loopback",
    "process_only_plan",
]
