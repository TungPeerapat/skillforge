"""Deterministic failure diagnosis.

Diagnosis is separate from execution: execution stays deterministic and
mechanical, while this module interprets a failed attempt with plain rules.
It works with no LLM and no network. The optional LLM diagnostician reuses the
same output model and the same approval gate.
"""

from __future__ import annotations

from dataclasses import dataclass

from skillforge.models import (
    Diagnosis,
    DiagnosisCategory,
    ExecutionAttempt,
    ExecutionPhase,
    ExecutionPlan,
    ExecutionStep,
    ProposedAction,
    ProposedActionKind,
    RiskLevel,
)
from skillforge.security.redaction import redact_text

_MAX_EVIDENCE_LINES = 6
_EVIDENCE_LINE_LIMIT = 240


@dataclass(frozen=True)
class _Rule:
    category: DiagnosisCategory
    patterns: tuple[str, ...]
    summary: str
    resolution: str
    confidence: float
    retry: bool = False


#: Ordered: the first matching rule wins. Patterns are matched case-insensitively
#: against stderr first, then stdout.
_RULES: tuple[_Rule, ...] = (
    _Rule(
        category=DiagnosisCategory.PORT_IN_USE,
        patterns=(
            "address already in use",
            "eaddrinuse",
            "port is already allocated",
            "address in use",
            "only one usage of each socket address",
        ),
        summary="the port is already bound by another process",
        resolution=(
            "stop the process that owns the port, choose a different port, "
            "or wait for the previous run to exit"
        ),
        confidence=0.9,
    ),
    _Rule(
        category=DiagnosisCategory.CONNECTION_REFUSED,
        patterns=(
            "connection refused",
            "econnrefused",
            "connection could not be established",
            "could not connect to server",
            "connect: connection refused",
            "failed to connect",
        ),
        summary="a dependency service refused the connection",
        resolution=(
            "make sure the dependency service is started and healthy before the app "
            "connects; if it was just starting, retrying after a short wait usually works"
        ),
        confidence=0.85,
        retry=True,
    ),
    _Rule(
        category=DiagnosisCategory.MISSING_ENV_VAR,
        patterns=(
            "missing required environment variable",
            "environment variable is not set",
            "env var",
            "keyerror",
            "not set in the environment",
            "undefined environment",
        ),
        summary="a required environment variable is missing",
        resolution=(
            "set the variable in the environment or a local .env file (never commit "
            "the value); SkillForge will not guess secrets or configuration"
        ),
        confidence=0.8,
    ),
    _Rule(
        category=DiagnosisCategory.MISSING_MODULE,
        patterns=(
            "no module named",
            "modulenotfounderror",
            "cannot find module",
            "module not found",
            "err_module_not_found",
        ),
        summary="a required package or module is not installed",
        resolution=(
            "run the repository's own setup/install step (listed in this plan) so the "
            "dependencies are available"
        ),
        confidence=0.9,
    ),
    _Rule(
        category=DiagnosisCategory.MISSING_EXECUTABLE,
        patterns=(
            "command not found",
            "is not recognized as an internal or external command",
            "executable file not found",
            "no such file or directory",
            "not found on path",
            "cannot find the file specified",
            "winerror 2",
            "cannot execute binary file",
        ),
        summary="an executable required by the command is not available",
        resolution=(
            "install the missing tool or run the command from an environment where it "
            "is on PATH; SkillForge does not install system tools"
        ),
        confidence=0.8,
    ),
    _Rule(
        category=DiagnosisCategory.PERMISSION_DENIED,
        patterns=("permission denied", "eacces", "access is denied", "operation not permitted"),
        summary="the command was not allowed to access a file or resource",
        resolution=(
            "check file ownership, executable bits, and whether the current user may "
            "perform this operation"
        ),
        confidence=0.9,
    ),
)

_TIMEOUT = _Rule(
    category=DiagnosisCategory.TIMEOUT,
    patterns=(),
    summary="the command did not finish within the step timeout",
    resolution=(
        "raise --step-timeout for slow steps, or check whether the command expects "
        "input or waits for a service that never becomes ready"
    ),
    confidence=0.9,
    retry=False,
)


def _lines_of(attempt: ExecutionAttempt) -> list[str]:
    lines: list[str] = []
    for text in (attempt.stderr_excerpt, attempt.stdout_excerpt):
        for line in text.splitlines():
            stripped = line.strip()
            if stripped:
                lines.append(stripped)
    return lines


def _evidence_lines(attempt: ExecutionAttempt, patterns: tuple[str, ...]) -> list[str]:
    matches: list[str] = []
    for line in _lines_of(attempt):
        lowered = line.lower()
        if any(pattern in lowered for pattern in patterns):
            candidate = redact_text(line[:_EVIDENCE_LINE_LIMIT])
            if candidate not in matches:
                matches.append(candidate)
        if len(matches) >= _MAX_EVIDENCE_LINES:
            break
    if matches:
        return matches
    # No pattern match: keep the last few lines as context, still redacted.
    tail = [redact_text(line[:_EVIDENCE_LINE_LIMIT]) for line in _lines_of(attempt)[-4:]]
    return tail


def _find_setup_step(plan: ExecutionPlan) -> ExecutionStep | None:
    for step in plan.steps:
        if step.phase is ExecutionPhase.SETUP and step.executable:
            return step
    return None


def diagnose(
    attempt: ExecutionAttempt,
    *,
    step: ExecutionStep,
    plan: ExecutionPlan,
    attempts_used: int,
    max_attempts: int,
) -> Diagnosis:
    """Produce a structured diagnosis for a failed attempt."""
    combined = f"{attempt.stderr_excerpt}\n{attempt.stdout_excerpt}".lower()

    if attempt.timed_out:
        rule = _TIMEOUT
        if step.phase is ExecutionPhase.LAUNCH:
            rule = _Rule(
                category=DiagnosisCategory.TIMEOUT,
                patterns=(),
                summary="the application did not become ready within the readiness timeout",
                resolution=(
                    "check the readiness evidence (endpoint, port, or log signal), raise "
                    "--ready-timeout for slow applications, or confirm the process actually "
                    "starts a server"
                ),
                confidence=0.9,
                retry=False,
            )
        return _build(
            rule,
            attempt,
            step,
            plan,
            attempts_used=attempts_used,
            max_attempts=max_attempts,
            retryable=False,
        )

    for rule in _RULES:
        if any(pattern in combined for pattern in rule.patterns):
            return _build(
                rule,
                attempt,
                step,
                plan,
                attempts_used=attempts_used,
                max_attempts=max_attempts,
                retryable=rule.retry,
            )

    unknown = _Rule(
        category=DiagnosisCategory.NONZERO_EXIT
        if attempt.exit_code is not None
        else DiagnosisCategory.UNKNOWN,
        patterns=(),
        summary=(
            f"the command exited with code {attempt.exit_code}"
            if attempt.exit_code is not None
            else "the command failed without a recognised signature"
        ),
        resolution=(
            "inspect the captured output and run the command manually to see the full error"
        ),
        confidence=0.4,
    )
    return _build(
        unknown,
        attempt,
        step,
        plan,
        attempts_used=attempts_used,
        max_attempts=max_attempts,
        retryable=False,
    )


def _build(
    rule: _Rule,
    attempt: ExecutionAttempt,
    step: ExecutionStep,
    plan: ExecutionPlan,
    *,
    attempts_used: int,
    max_attempts: int,
    retryable: bool,
) -> Diagnosis:
    actions: list[ProposedAction] = []
    remaining = max(0, max_attempts - attempts_used)
    if retryable and remaining > 0:
        delay = min(2.0 * attempts_used, 10.0)
        actions.append(
            ProposedAction(
                kind=ProposedActionKind.RETRY_STEP,
                description=(
                    f"re-run '{step.command}' after a {delay:.0f}s wait "
                    f"({remaining} attempt(s) left)"
                ),
                command=step.command,
                risk=step.risk,
                evidence=list(step.evidence),
                delay_seconds=delay,
                step_id=step.id,
            )
        )
    if rule.category is DiagnosisCategory.MISSING_MODULE:
        setup = _find_setup_step(plan)
        if setup is not None:
            actions.append(
                ProposedAction(
                    kind=ProposedActionKind.RUN_COMMAND,
                    description=(
                        f"run the repository's setup step before retrying: '{setup.command}'"
                    ),
                    command=setup.command,
                    risk=setup.risk,
                    evidence=list(setup.evidence),
                    step_id=setup.id,
                )
            )
    if not actions:
        actions.append(
            ProposedAction(
                kind=ProposedActionKind.MANUAL,
                description=(
                    "no automatic correction is proposed; resolve the issue described "
                    "above, then re-run `skillforge learn`"
                ),
                risk=RiskLevel.REVIEW,
            )
        )

    lines = _evidence_lines(
        attempt,
        rule.patterns if rule.patterns else (),
    )
    summary = rule.summary
    return Diagnosis(
        category=rule.category,
        summary=summary,
        confidence=min(0.99, rule.confidence if rule.patterns or attempt.timed_out else 0.4),
        source="deterministic",
        evidence_lines=lines,
        suggested_resolution=rule.resolution,
        proposed_actions=actions,
        notes=[f"step '{step.id}' ({step.purpose}); exit code {attempt.exit_code}"],
    )


__all__ = ["diagnose"]
