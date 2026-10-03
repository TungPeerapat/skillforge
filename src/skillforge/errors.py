"""Error hierarchy and process exit codes.

Exit codes are part of the CLI contract so scripts can branch on failures:

===== ==========================================================
Code  Meaning
===== ==========================================================
0     success
1     analysis/validation completed but found errors
2     invalid usage or configuration
3     environment problem (missing tool, unwritable path)
4     provider or external-transmission failure
5     security refusal (consent missing, unsafe path, dangerous command)
===== ==========================================================
"""

from __future__ import annotations

from typing import ClassVar


class SkillForgeError(Exception):
    """Base class for all expected SkillForge failures."""

    exit_code: ClassVar[int] = 1

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class UsageError(SkillForgeError):
    """The user asked for something impossible or contradictory."""

    exit_code = 2


class ConfigError(SkillForgeError):
    """Configuration could not be loaded or is invalid."""

    exit_code = 2


class AnalysisError(SkillForgeError):
    """Repository analysis failed."""

    exit_code = 1


class NotFoundError(SkillForgeError):
    """A requested path, skill, or scenario does not exist."""

    exit_code = 1


class SkillExistsError(UsageError):
    """A skill directory already exists and overwrite was not requested."""

    exit_code = 2


class ExampleError(SkillForgeError):
    """An internal invariant was violated (a bug in SkillForge)."""

    exit_code = 1


class EnvironmentError_(SkillForgeError):
    """A required external tool or resource is missing.

    Named with a trailing underscore to avoid shadowing the builtin
    :class:`EnvironmentError` (an alias of :class:`OSError`).
    """

    exit_code = 3


class ProviderError(SkillForgeError):
    """An LLM provider failed or is unavailable."""

    exit_code = 4


class ExternalTransmissionError(SkillForgeError):
    """Refused to send repository content to an external service."""

    exit_code = 5


class SecurityRefusal(SkillForgeError):
    """A security guard rejected the operation."""

    exit_code = 5
