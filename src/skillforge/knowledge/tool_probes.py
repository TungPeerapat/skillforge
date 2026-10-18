"""Default-off K10 probe policy and injectable backend; no live backend shipped.

Even --help may import plugins or dependency code. Production execution remains
disabled pending the K14 consent integration/security review. Offline tests can
inject a backend to prove argv, version, budget, cwd and cache behavior.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol

from skillforge.knowledge.extractors.cli import clean_help
from skillforge.security.redaction import redact_text

_COMMANDS: dict[str, frozenset[tuple[str, ...]]] = {
    "pytest": frozenset({()}),
    "ruff": frozenset({(), ("check",), ("format",)}),
    "next": frozenset({(), ("build",), ("dev",), ("start",)}),
    "dotnet": frozenset({(), ("build",), ("test",), ("run",), ("publish",)}),
}
_VERSION_PATTERNS = {
    "pytest": re.compile(r"^pytest\s+(\d+\.\d+\.\d+(?:[\w.+-]*)?)$", re.I),
    "ruff": re.compile(r"^ruff\s+(\d+\.\d+\.\d+(?:[\w.+-]*)?)$", re.I),
    "next": re.compile(r"^Next\.js\s+v?(\d+\.\d+\.\d+(?:[\w.+-]*)?)$", re.I),
    "dotnet": re.compile(r"^(\d+\.\d+\.\d+(?:[\w.+-]*)?)$"),
}


@dataclass(frozen=True)
class CliProbePolicy:
    enabled: bool = False
    timeout_seconds: float = 8.0
    max_output_bytes: int = 64 * 1024

    def __post_init__(self) -> None:
        if not 0 < self.timeout_seconds <= 30 or not 1 <= self.max_output_bytes <= 1024 * 1024:
            raise ValueError("invalid probe budgets")


@dataclass(frozen=True)
class ProbeRequest:
    argv: tuple[str, ...]
    cwd: Path
    environment: dict[str, str]
    timeout_seconds: float
    max_output_bytes: int


@dataclass(frozen=True)
class ProbeResponse:
    exit_code: int
    output: str = ""
    timed_out: bool = False
    truncated: bool = False


class ProbeBackend(Protocol):
    def run(self, request: ProbeRequest) -> ProbeResponse:
        """Backend must enforce budgets and process-tree cleanup itself."""
        ...


@dataclass(frozen=True)
class ProbeObservation:
    ok: bool
    output: str = ""
    version: str | None = None
    reason: str = ""
    executable_identity: str = ""


@dataclass
class ToolProbeService:
    policy: CliProbePolicy = field(default_factory=CliProbePolicy)
    backend: ProbeBackend | None = None
    _cache: dict[tuple[str, str, tuple[str, ...]], ProbeObservation] = field(
        default_factory=dict, init=False
    )

    def inspect(
        self,
        tool: str,
        *,
        executable: Path,
        repository: Path,
        expected_version: str,
        command_path: tuple[str, ...] = (),
    ) -> ProbeObservation:
        if not self.policy.enabled:
            return ProbeObservation(False, reason="probing requires explicit invocation consent")
        if self.backend is None:
            return ProbeObservation(False, reason="live probing is not enabled in K10")
        backend = self.backend
        if tool not in _COMMANDS or command_path not in _COMMANDS[tool]:
            return ProbeObservation(False, reason="tool/argv is not allowlisted")
        if not executable.is_absolute():
            return ProbeObservation(False, reason="explicit absolute executable identity required")
        executable = executable.resolve()
        if executable.is_relative_to(repository.resolve()):
            return ProbeObservation(False, reason="repository executables are refused")
        identity = hashlib.sha256((tool + ":" + str(executable)).encode()).hexdigest()
        key = (identity, expected_version, command_path)
        if key in self._cache:
            return self._cache[key]

        def request(arguments: tuple[str, ...]) -> ProbeObservation:
            # No repository cwd, inherited secrets, PYTHONPATH, or shell.
            with TemporaryDirectory(prefix="skillforge-probe-") as temporary:
                response = backend.run(
                    ProbeRequest(
                        argv=(str(executable), *arguments),
                        cwd=Path(temporary),
                        environment={
                            "NO_COLOR": "1",
                            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                            "PYTHONNOUSERSITE": "1",
                            "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1",
                            "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
                        },
                        timeout_seconds=self.policy.timeout_seconds,
                        max_output_bytes=self.policy.max_output_bytes,
                    )
                )
            if response.timed_out or response.truncated or response.exit_code != 0:
                return ProbeObservation(False, reason="probe failed, timed out, or truncated")
            if len(response.output.encode("utf-8")) > self.policy.max_output_bytes:
                return ProbeObservation(False, reason="probe output budget exceeded")
            return ProbeObservation(
                True, output=redact_text(clean_help(response.output)), executable_identity=identity
            )

        version_key = (identity, expected_version, ("--version",))
        version_result = self._cache.get(version_key)
        if version_result is None:
            version_result = request(("--version",))
            if version_result.ok:
                match = _VERSION_PATTERNS[tool].fullmatch(version_result.output.strip())
                if not match or match[1] != expected_version:
                    version_result = ProbeObservation(
                        False, reason="tool version mismatch or unknown version"
                    )
                else:
                    version_result = ProbeObservation(
                        True, version=match[1], executable_identity=identity
                    )
            self._cache[version_key] = version_result
        if not version_result.ok:
            self._cache[key] = version_result
            return version_result
        result = request((*command_path, "--help"))
        result = ProbeObservation(
            result.ok,
            output=result.output,
            version=version_result.version,
            reason=result.reason,
            executable_identity=identity,
        )
        self._cache[key] = result
        return result
