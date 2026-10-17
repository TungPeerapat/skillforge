"""Generalization: turn a successful session into reusable, portable knowledge.

Raw execution logs are never copied into skills. Learned steps are verified
commands with provenance; wait times become readiness requirements; failures
become troubleshooting entries only when a later attempt actually resolved them.
All text passes redaction and strips machine-specific details (absolute paths,
usernames, transient identifiers).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from skillforge.models import (
    ExecutionAttempt,
    ExecutionPhase,
    ExecutionPlan,
    ExecutionStep,
    LearnedStep,
    LearnedWorkflow,
    LearningSession,
    ReadinessKind,
    ReadinessRequirement,
    SkillFile,
    StepAction,
    TroubleshootingEntry,
)
from skillforge.security.paths import is_absolute_local_path
from skillforge.security.redaction import redact_text

_WINDOWS_USER_RE = re.compile(r"(?i)([a-z]:\\\\?users\\\\?)([^\\/\s:]+)")
_POSIX_USER_RE = re.compile(r"(?i)(/(?:home|users)/)([^/\s:]+)")
_ISO_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?")


def sanitize_text(text: str) -> str:
    """Remove usernames and transient timestamps from learned prose."""
    if not text:
        return text
    result = _WINDOWS_USER_RE.sub(r"\1<user>", text)
    result = _POSIX_USER_RE.sub(r"\1<user>", result)
    result = _ISO_TIMESTAMP_RE.sub("<timestamp>", result)
    return redact_text(result)


def _is_machine_specific(command: str) -> bool:
    return is_absolute_local_path(command)


def _verified_step_ids(session: LearningSession) -> set[str]:
    succeeded = {attempt.step_id for attempt in session.attempts if attempt.succeeded}
    if session.verification is not None and session.verification.success:
        for step in session.plan.steps:
            if step.phase is ExecutionPhase.LAUNCH:
                succeeded.add(step.id)
    return succeeded


def _learned_step(step: ExecutionStep, *, verified: bool, prerequisite: str = "") -> LearnedStep:
    return LearnedStep(
        id=step.id,
        phase=step.phase,
        purpose=step.purpose,
        command=sanitize_text(step.command),
        cwd=step.cwd,
        risk=step.risk,
        verified=verified,
        evidence=list(step.evidence),
        prerequisite=sanitize_text(prerequisite),
        notes=[sanitize_text(note) for note in step.notes],
    )


def _readiness_requirement(session: LearningSession) -> ReadinessRequirement | None:
    readiness = None
    for step in session.plan.steps:
        if step.readiness is not None:
            readiness = step.readiness
            break
    if readiness is None:
        return None
    verification = session.verification
    verified = bool(verification and verification.success)
    if readiness.kind is ReadinessKind.HTTP and readiness.url:
        description = f"the application answers `GET {readiness.url}` with HTTP 2xx"
    elif readiness.kind is ReadinessKind.TCP:
        description = f"the application accepts TCP connections on port {readiness.port}"
    elif readiness.kind is ReadinessKind.LOG_SIGNAL:
        description = "the application prints a recognised startup line"
    else:
        description = "the application process stays alive"
    return ReadinessRequirement(
        description=description,
        kind=readiness.kind,
        confidence=readiness.confidence,
        verified=verified,
        target=readiness.target,
        evidence=list(readiness.evidence),
    )


def _prerequisite_for(step: ExecutionStep, plan: ExecutionPlan) -> str:
    phases_before = {
        ExecutionPhase.SETUP: (),
        ExecutionPhase.SERVICES: (ExecutionPhase.SETUP,),
        ExecutionPhase.MIGRATION: (ExecutionPhase.SETUP, ExecutionPhase.SERVICES),
        ExecutionPhase.LAUNCH: (
            ExecutionPhase.SETUP,
            ExecutionPhase.SERVICES,
            ExecutionPhase.MIGRATION,
        ),
    }
    wanted = phases_before.get(step.phase, ())
    labels: list[str] = []
    for candidate in plan.steps:
        if candidate.phase in wanted and candidate.action is StepAction.COMMAND:
            labels.append(candidate.command)
    if not labels:
        return ""
    return "after: " + "; ".join(dict.fromkeys(labels))


def _troubleshooting(session: LearningSession) -> list[TroubleshootingEntry]:
    """Resolved failures only: failure knowledge with execution evidence."""
    entries: list[TroubleshootingEntry] = []
    attempts_by_step: dict[str, list[ExecutionAttempt]] = {}
    for attempt in session.attempts:
        attempts_by_step.setdefault(attempt.step_id, []).append(attempt)
    for step_id, attempts in attempts_by_step.items():
        failures = [attempt for attempt in attempts if not attempt.succeeded]
        successes = [attempt for attempt in attempts if attempt.succeeded]
        if not failures or not successes:
            continue
        first_failure = failures[0]
        diagnosis = first_failure.diagnosis
        if diagnosis is None:
            continue
        resolved_attempt = successes[0]
        symptom = sanitize_text(diagnosis.summary)
        resolution = sanitize_text(diagnosis.suggested_resolution)
        if resolved_attempt.attempt_number > first_failure.attempt_number:
            resolution = (
                f"{resolution} (a delayed retry of the same command succeeded on "
                f"attempt {resolved_attempt.attempt_number})"
            )
        entries.append(
            TroubleshootingEntry(
                symptom=symptom,
                resolution=resolution,
                category=diagnosis.category,
                evidence=(
                    f"Learning session {session.id}, step {step_id}, attempts "
                    f"{first_failure.attempt_number}-{resolved_attempt.attempt_number}"
                ),
                attempts=[first_failure.attempt_number, resolved_attempt.attempt_number],
            )
        )
    return entries


def generalize(session: LearningSession) -> LearnedWorkflow:
    """Derive verified knowledge from a successful session."""
    if session.verification is None or not session.verification.success:
        raise ValueError("cannot generalize a session that was not verified")
    succeeded = _verified_step_ids(session)
    steps: list[LearnedStep] = []
    unverified: list[LearnedStep] = []
    for step in session.plan.steps:
        if step.action is not StepAction.COMMAND or step.excluded:
            continue
        if _is_machine_specific(step.command):
            unverified.append(
                _learned_step(
                    step,
                    verified=False,
                    prerequisite="machine-specific command removed during generalization",
                )
            )
            continue
        if step.id in succeeded:
            steps.append(
                _learned_step(
                    step, verified=True, prerequisite=_prerequisite_for(step, session.plan)
                )
            )
        else:
            unverified.append(_learned_step(step, verified=False))
    readiness = _readiness_requirement(session)
    notes: list[str] = []
    if unverified:
        notes.append(
            f"{len(unverified)} step(s) were not verified in this session and remain inferred."
        )
    if session.plan.excluded_dangerous:
        notes.append(
            f"{len(session.plan.excluded_dangerous)} destructive command(s) were excluded and "
            "were never executed."
        )
    return LearnedWorkflow(
        target=session.target,
        session_id=session.id,
        repository=session.repository,
        verified_at=session.completed_at or datetime.now(UTC),
        steps=steps,
        unverified_steps=unverified,
        readiness=[readiness] if readiness is not None else [],
        troubleshooting=_troubleshooting(session),
        notes=notes,
    )


def _fence(lines: list[str]) -> list[str]:
    if not lines:
        return []
    return ["```bash", *lines, "```", ""]


def render_verified_workflow(workflow: LearnedWorkflow) -> SkillFile:
    """`references/verified-workflow.md`: only what execution actually proved."""
    lines: list[str] = [
        "# Verified workflow",
        "",
        "> This workflow was established by controlled execution, not inference.",
        f"> Session: `{workflow.session_id}` · Verified: {workflow.verified_at.date().isoformat()}",
        "",
        "Steps marked **verified** ran successfully in this repository. Steps marked "
        "*inferred* were not executed and remain unverified.",
        "",
    ]

    if workflow.steps:
        lines.append("## Verified steps")
        lines.append("")
        for step in workflow.steps:
            lines.append(f"### {step.id} — {step.phase.label}")
            lines.append("")
            if step.prerequisite:
                lines.append(f"Prerequisite: {step.prerequisite}")
                lines.append("")
            lines.extend(_fence([step.command]))
            if step.evidence:
                sources = ", ".join(f"`{item.label}`" for item in step.evidence)
                lines.append(f"Evidence: {sources}")
                lines.append("")

    readiness_lines: list[str] = []
    for requirement in workflow.readiness:
        if requirement.verified:
            readiness_lines.append(f"- Verified: {requirement.description}")
        else:
            readiness_lines.append(f"- Not verified: {requirement.description}")
    if readiness_lines:
        lines.append("## Readiness requirements")
        lines.append("")
        lines.extend(readiness_lines)
        lines.append("")

    if workflow.troubleshooting:
        lines.append("## Known failure modes")
        lines.append("")
        for entry in workflow.troubleshooting:
            lines.append(f"- **{entry.symptom}**: {entry.resolution}")
        lines.append("")

    if workflow.unverified_steps:
        lines.append("## Inferred (not verified)")
        lines.append("")
        for step in workflow.unverified_steps:
            lines.append(f"- `{step.command}` ({step.phase.label})")
        lines.append("")

    lines.extend(
        [
            "## Shutdown / cleanup",
            "",
            "Processes started by SkillForge during learning were terminated after "
            "verification. Run long-lived commands in the foreground and stop them with "
            "Ctrl+C so nothing is left running.",
            "",
            "## Provenance",
            "",
            f"- learning session: `.skillforge/sessions/{workflow.session_id}.json`",
            f"- repository: `{workflow.repository}`",
        ]
    )
    if workflow.notes:
        lines.append("")
        for note in workflow.notes:
            lines.append(f"- {note}")
    lines.append("")
    return SkillFile(
        path="references/verified-workflow.md",
        content="\n".join(lines),
        description="Workflow established by controlled execution, with session provenance.",
    )


def render_troubleshooting(workflow: LearnedWorkflow) -> SkillFile | None:
    """`references/troubleshooting.md`: failure knowledge that was actually resolved."""
    if not workflow.troubleshooting:
        return None
    lines: list[str] = [
        "# Troubleshooting (learned)",
        "",
        "These problems were observed during controlled execution and resolved by the "
        "recorded procedure. They contain no machine-specific paths or secrets.",
        "",
    ]
    for entry in workflow.troubleshooting:
        lines.append(f"## {entry.symptom}")
        lines.append("")
        lines.append(f"Problem: {entry.symptom}.")
        lines.append("")
        lines.append(f"Resolution: {entry.resolution}")
        lines.append("")
        lines.append(f"Evidence: {entry.evidence}.")
        lines.append("")
    return SkillFile(
        path="references/troubleshooting.md",
        content="\n".join(lines),
        description="Failure modes observed and resolved during learning sessions.",
    )


def verification_section(workflow: LearnedWorkflow) -> str:
    """Concise SKILL.md section pointing at verified knowledge."""
    lines = [
        "## Verification",
        "",
        f"- Status: VERIFIED (controlled execution, session `{workflow.session_id}`)",
        f"- Verified at: {workflow.verified_at.date().isoformat()}",
        "- Verified workflow: `references/verified-workflow.md`",
    ]
    if workflow.troubleshooting:
        lines.append("- Learned failure modes: `references/troubleshooting.md`")
    lines.extend(
        [
            "",
            "Only steps that succeeded during the learning session are marked verified; "
            "everything else is explicitly labelled as inferred.",
            "",
        ]
    )
    return "\n".join(lines)


def replace_verification_section(body: str, section: str) -> str:
    """Idempotently replace or append the ``## Verification`` section."""
    lines = body.splitlines()
    start: int | None = None
    end = len(lines)
    for index, line in enumerate(lines):
        if line.strip().lower() == "## verification":
            start = index
            continue
        if start is not None and line.startswith("## "):
            end = index
            break
    replacement = section.strip().splitlines()
    if start is None:
        combined = [*lines, "", *replacement]
    else:
        combined = [*lines[:start], *replacement, "", *lines[end:]]
    return "\n".join(combined).rstrip() + "\n"


__all__ = [
    "generalize",
    "render_troubleshooting",
    "render_verified_workflow",
    "replace_verification_section",
    "sanitize_text",
    "verification_section",
]
