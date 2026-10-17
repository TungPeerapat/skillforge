"""Local execution backend.

This backend runs repository commands as argv lists with ``shell=False``. It is
**not a sandbox**: commands run with the same user, environment, filesystem and
network access as SkillForge itself. What it enforces is narrower but real:

* argv-only execution; shell syntax was already rejected at plan time;
* the working directory must stay inside the repository root;
* bounded timeouts and output buffers;
* process-group termination so nothing is left orphaned;
* a compatibility shim for Windows ``.cmd`` launchers (``npm``, ``npx``, …)
  that is only reachable after shell metacharacters were rejected.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import time
from pathlib import Path

from skillforge.errors import SecurityRefusal
from skillforge.execution.base import (
    ExecutionBackend,
    ExecutionOutcome,
    ExecutionRequest,
    ManagedProcessLike,
)
from skillforge.execution.process import ManagedProcess
from skillforge.execution.readiness import evaluate
from skillforge.models import DEFAULT_OUTPUT_LIMIT_BYTES, ReadinessPlan, VerificationResult

_WINDOWS_BATCH_SUFFIXES = (".cmd", ".bat")
_WINDOWS_UNSUPPORTED_SUFFIXES = (".ps1", ".vbs")


class LocalExecutionBackend(ExecutionBackend):
    """Run commands locally, safely bounded, with no isolation claim."""

    id = "local"

    def __init__(
        self,
        repo_root: Path,
        *,
        default_timeout: float = 120.0,
        output_limit: int = DEFAULT_OUTPUT_LIMIT_BYTES,
    ) -> None:
        self._repo_root = repo_root.resolve()
        self._default_timeout = default_timeout
        self._output_limit = output_limit
        self._processes: list[ManagedProcess] = []

    # ------------------------------------------------------------------- paths
    def resolve_cwd(self, cwd: str) -> Path:
        """Resolve ``cwd`` under the repository root, refusing escapes."""
        candidate = (self._repo_root / (cwd or ".")).resolve()
        try:
            candidate.relative_to(self._repo_root)
        except ValueError as exc:
            raise SecurityRefusal(
                f"refusing to run with a working directory outside the repository: {cwd}",
                hint="execution steps must stay inside the analysed repository",
            ) from exc
        if not candidate.is_dir():
            raise SecurityRefusal(f"working directory does not exist: {candidate}")
        return candidate

    # -------------------------------------------------------------- execution
    def run(self, request: ExecutionRequest) -> ExecutionOutcome:
        started = time.monotonic()
        try:
            cwd = self.resolve_cwd(str(request.cwd))
        except SecurityRefusal as exc:
            return ExecutionOutcome(
                argv=request.argv,
                cwd=str(request.cwd),
                blocked_reason=exc.message,
                duration_ms=round((time.monotonic() - started) * 1000, 3),
            )
        missing = self._missing_executable(request.argv, cwd)
        if missing is not None:
            return ExecutionOutcome(
                argv=request.argv,
                cwd=str(cwd),
                start_error=missing,
                duration_ms=round((time.monotonic() - started) * 1000, 3),
            )
        try:
            process = self._spawn(request, cwd)
        except OSError as exc:
            return ExecutionOutcome(
                argv=request.argv,
                cwd=str(cwd),
                start_error=str(exc),
                duration_ms=round((time.monotonic() - started) * 1000, 3),
            )
        timeout = request.timeout or self._default_timeout
        timed_out = False
        exit_code = process.wait(timeout)
        if exit_code is None:
            timed_out = True
            process.terminate(grace=3.0)
            exit_code = process.returncode
        outcome = ExecutionOutcome(
            argv=request.argv,
            cwd=str(cwd),
            exit_code=exit_code,
            timed_out=timed_out,
            duration_ms=round((time.monotonic() - started) * 1000, 3),
            stdout=process.stdout_text(),
            stderr=process.stderr_text(),
            truncated=process.truncated,
        )
        self._forget(process)
        return outcome

    def start(self, request: ExecutionRequest) -> ManagedProcess:
        """Start a long-running command; callers must eventually terminate it."""
        cwd = self.resolve_cwd(str(request.cwd))
        missing = self._missing_executable(request.argv, cwd)
        if missing is not None:
            raise FileNotFoundError(missing)
        process = self._spawn(request, cwd)
        self._processes.append(process)
        return process

    def verify(
        self,
        readiness: ReadinessPlan,
        process: ManagedProcessLike | None,
        *,
        timeout: float,
    ) -> VerificationResult:
        return evaluate(readiness, process, timeout=timeout)

    def cleanup(self) -> None:
        for process in list(self._processes):
            try:
                process.terminate(grace=3.0)
            except Exception:  # pragma: no cover - cleanup must never raise
                continue
            finally:
                self._forget(process)

    def describe(self) -> str:
        return (
            "local execution (argv-only, no shell, cwd confined to the repository; "
            "runs with your user account, environment and network — not a sandbox)"
        )

    # --------------------------------------------------------------- internals
    def _spawn(self, request: ExecutionRequest, cwd: Path) -> ManagedProcess:
        argv = self._exec_argv(request.argv)
        limit = request.output_limit or self._output_limit
        return ManagedProcess(tuple(argv), cwd=cwd, output_limit=limit)

    def _missing_executable(self, argv: tuple[str, ...], cwd: Path) -> str | None:
        if not argv:  # pragma: no cover - guarded by the plan builder
            return "empty argv"
        program = argv[0]
        if "/" in program or "\\" in program:
            candidate = (cwd / program).resolve()
            return None if candidate.exists() else f"{program} not found in {cwd}"
        return None if shutil.which(program) else f"{program} not found on PATH"

    def _exec_argv(self, argv: tuple[str, ...]) -> list[str]:
        """Map the argv to something the OS can actually execute."""
        if os.name != "nt":  # pragma: no cover - platform specific
            return list(argv)
        program = argv[0]
        resolved = program
        if "/" not in program and "\\" not in program:
            resolved = shutil.which(program) or program
        lowered = resolved.lower()
        if lowered.endswith(_WINDOWS_UNSUPPORTED_SUFFIXES):
            raise SecurityRefusal(
                f"refusing to execute '{program}': PowerShell/VBScript launchers are not "
                "supported by the local backend",
                hint="run the command manually or add an .exe/.cmd entry point",
            )
        if lowered.endswith(_WINDOWS_BATCH_SUFFIXES):
            # Standard Windows behaviour for .cmd launchers (npm, npx, ...).
            # Shell metacharacters were rejected before this point, so cmd.exe
            # cannot interpret operators, expansions, or redirections.
            return ["cmd.exe", "/d", "/s", "/c", subprocess.list2cmdline(list(argv))]
        return list(argv)

    def _forget(self, process: ManagedProcess) -> None:
        with contextlib.suppress(ValueError):
            self._processes.remove(process)


__all__ = ["LocalExecutionBackend"]
