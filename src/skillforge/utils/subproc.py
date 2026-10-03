"""Subprocess probing for the ``doctor`` command.

This module is the **only** place in SkillForge that executes external
processes, and it is restricted to hardcoded ``argv`` lists with
``shell=False``. Repository-defined commands are never passed here.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass

DEFAULT_TIMEOUT = 8.0


@dataclass(frozen=True)
class ProbeResult:
    """Outcome of probing a single executable."""

    ok: bool
    output: str = ""
    error: str | None = None
    path: str | None = None

    @property
    def version_line(self) -> str:
        for line in self.output.splitlines():
            line = line.strip()
            if line:
                return line
        return ""


def which(executable: str) -> str | None:
    """Locate an executable on ``PATH`` (no execution)."""
    return shutil.which(executable)


def probe(argv: Sequence[str], *, timeout: float = DEFAULT_TIMEOUT) -> ProbeResult:
    """Run a fixed command and capture its first lines.

    Callers must pass literal argument lists; nothing derived from repository
    content may reach this function.
    """
    if not argv:  # pragma: no cover - programming error
        raise ValueError("probe() requires a non-empty argv")
    executable = which(argv[0])
    if executable is None:
        return ProbeResult(ok=False, error=f"{argv[0]} not found on PATH")
    try:
        completed = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return ProbeResult(ok=False, error=f"{argv[0]} timed out", path=executable)
    except OSError as exc:
        return ProbeResult(ok=False, error=str(exc), path=executable)
    output = (completed.stdout or completed.stderr or "").strip()
    if completed.returncode != 0 and not output:
        return ProbeResult(ok=False, error=f"exit code {completed.returncode}", path=executable)
    return ProbeResult(ok=completed.returncode == 0, output=output, path=executable)
