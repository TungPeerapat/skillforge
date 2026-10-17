"""Execution-plan construction.

A plan converts evidence-backed workflows into ordered, bounded, risk-labelled
steps. It never invents a command: every shell command in a plan comes from
``RepositoryProfile.commands`` and keeps its ``Evidence``. DANGEROUS commands
are listed as excluded and can never be executed. Commands with shell syntax
are not representable as argv and are reported as unschedulable instead.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from skillforge.errors import AnalysisError
from skillforge.learning import evidence as evidence_mod
from skillforge.learning.fingerprint import repository_fingerprint, workflow_fingerprint
from skillforge.models import (
    Command,
    ExecutionPhase,
    ExecutionPlan,
    ExecutionStep,
    ReadinessKind,
    RepositoryProfile,
    RiskLevel,
    StepAction,
)
from skillforge.models.workflow import CommandPurpose, CommandSource
from skillforge.security.argv import split_argv

_SOURCE_RANK: dict[str, int] = {
    CommandSource.PACKAGE_SCRIPT.value: 0,
    CommandSource.MAKEFILE.value: 1,
    CommandSource.JUSTFILE.value: 2,
    CommandSource.PROCFILE.value: 2,
    CommandSource.TASKFILE.value: 3,
    CommandSource.COMPOSE.value: 4,
    CommandSource.CONFIG.value: 5,
    CommandSource.README.value: 6,
    CommandSource.DOCS.value: 7,
    CommandSource.DOCKERFILE.value: 8,
    CommandSource.OTHER.value: 9,
    CommandSource.CI.value: 10,
}

_CHILD_ACTIONS: dict[ReadinessKind, StepAction] = {
    ReadinessKind.HTTP: StepAction.HTTP_GET,
    ReadinessKind.TCP: StepAction.WAIT_PORT,
    ReadinessKind.LOG_SIGNAL: StepAction.WAIT_LOG,
    ReadinessKind.PROCESS: StepAction.WAIT_PROCESS,
}

_MIGRATION_HINTS = ("upgrade", "migrate", "up", "apply", "head", "deploy")


@dataclass(frozen=True)
class PlanOptions:
    """Knobs that affect planning, not execution consent."""

    step_timeout: float = 120.0
    only_safe: bool = False
    max_setup_steps: int = 2
    max_service_steps: int = 3
    max_migration_steps: int = 1
    force_target: str | None = None


@dataclass
class _Selection:
    selected: list[Command] = field(default_factory=list)
    excluded_shell: list[str] = field(default_factory=list)
    skipped_review: list[str] = field(default_factory=list)


def _candidates(
    profile: RepositoryProfile,
    *,
    purposes: tuple[CommandPurpose, ...],
    exclude_sources: tuple[str, ...] = (),
) -> list[Command]:
    wanted = {purpose.value for purpose in purposes}
    result: list[Command] = []
    for command in profile.commands:
        if command.purpose.value not in wanted:
            continue
        if command.source.value in exclude_sources:
            continue
        if command.certainty.value == "unknown" or command.placeholders:
            continue
        if command.risk.is_dangerous:
            continue
        result.append(command)
    result.sort(
        key=lambda command: (
            _SOURCE_RANK.get(command.source.value, 9),
            -command.confidence,
            command.command,
        )
    )
    return result


def _select(
    command_list: list[Command],
    *,
    limit: int,
    only_safe: bool,
    skip: set[str],
) -> _Selection:
    selection = _Selection()
    seen: set[str] = set()
    for command in command_list:
        if command.command in skip or command.command in seen:
            continue
        seen.add(command.command)
        if split_argv(command.command) is None:
            selection.excluded_shell.append(command.command)
            continue
        if only_safe and not command.risk.is_safe:
            selection.skipped_review.append(command.command)
            continue
        selection.selected.append(command)
        if len(selection.selected) >= limit:
            break
    return selection


def _is_compose_run(command: Command) -> bool:
    argv = split_argv(command.command)
    return bool(argv and len(argv) >= 3 and argv[1] == "compose" and argv[2] == "up")


def _is_detached_compose(command: Command) -> bool:
    if not _is_compose_run(command):
        return False
    argv = split_argv(command.command) or []
    return any(token in ("-d", "--detach") for token in argv[3:])


def _compose_specificity(command: Command) -> int:
    """Prefer ``up -d postgres`` over plain ``up -d`` (more specificity first)."""
    argv = split_argv(command.command) or []
    try:
        index = argv.index("up")
    except ValueError:  # pragma: no cover - guarded by _is_detached_compose
        index = len(argv)
    extra = [token for token in argv[index + 1 :] if not token.startswith("-")]
    return -len(extra)


def _migration_rank(command: Command) -> int:
    lowered = command.command.lower()
    return 0 if any(hint in lowered for hint in _MIGRATION_HINTS) else 1


def _step_from_command(
    command: Command,
    *,
    step_id: str,
    phase: ExecutionPhase,
    timeout: float,
    long_running: bool = False,
    expected: str = "",
) -> ExecutionStep:
    argv = split_argv(command.command) or []
    return ExecutionStep(
        id=step_id,
        phase=phase,
        purpose=command.purpose.value,
        action=StepAction.COMMAND,
        command=command.command,
        argv=argv,
        cwd=command.cwd or ".",
        evidence=list(command.evidence),
        risk=command.risk,
        risk_reasons=list(command.risk_reasons),
        timeout_seconds=timeout,
        expected_outcome=expected or command.purpose.label,
        long_running=long_running,
        notes=list(command.notes),
    )


def _risk_summary(steps: list[ExecutionStep]) -> dict[str, int]:
    summary = {"SAFE": 0, "REVIEW_REQUIRED": 0, "DANGEROUS": 0}
    for step in steps:
        if step.excluded or step.action is not StepAction.COMMAND:
            continue
        summary[step.risk.label] = summary.get(step.risk.label, 0) + 1
    return summary


def build_execution_plan(
    profile: RepositoryProfile,
    *,
    scan_contents: dict[str, str],
    options: PlanOptions | None = None,
    target: str = "project-runner",
) -> ExecutionPlan:
    """Build the plan for ``target`` from an analysed repository profile."""
    active = options or PlanOptions()
    if target != "project-runner":
        raise AnalysisError(
            f"learning target '{target}' is not supported in v0.2",
            hint="v0.2 supports: project-runner",
        )

    notes: list[str] = []
    dangerous = sorted(
        {command.command for command in profile.commands if command.risk.is_dangerous}
    )
    skip: set[str] = set()

    setup = _select(
        _candidates(profile, purposes=(CommandPurpose.SETUP,), exclude_sources=("ci",)),
        limit=active.max_setup_steps,
        only_safe=active.only_safe,
        skip=skip,
    )
    services = _select(
        sorted(
            (
                command
                for command in _candidates(profile, purposes=(CommandPurpose.RUN,))
                if _is_detached_compose(command)
            ),
            key=lambda command: (_compose_specificity(command), command.command),
        ),
        limit=active.max_service_steps,
        only_safe=active.only_safe,
        skip=skip,
    )
    migration_candidates = _candidates(
        profile, purposes=(CommandPurpose.MIGRATE,), exclude_sources=("ci",)
    )
    migration_candidates.sort(key=lambda command: (_migration_rank(command), command.command))
    migration = _select(
        migration_candidates,
        limit=active.max_migration_steps,
        only_safe=active.only_safe,
        skip=skip,
    )

    launch_candidates = _candidates(
        profile, purposes=(CommandPurpose.RUN,), exclude_sources=("ci",)
    )
    launch_candidates = [
        command
        for command in launch_candidates
        if not _is_compose_run(command)
        and command.command not in {step.command for step in services.selected}
    ]
    launch = _select(launch_candidates, limit=1, only_safe=active.only_safe, skip=skip)
    if not launch.selected:
        foreground = [
            command.command
            for command in _candidates(profile, purposes=(CommandPurpose.RUN,))
            if _is_compose_run(command)
        ]
        if foreground:
            notes.append(
                "Only foreground compose run commands were found; start containers manually "
                "or add a detached 'docker compose up -d' command to the repository."
            )
        raise AnalysisError(
            "No runnable entry point with repository evidence was found for project-runner.",
            hint=(
                "add a documented run command (package script, Makefile target, Procfile, "
                "README code block) or run `skillforge analyze` to inspect what was detected"
            ),
        )

    steps: list[ExecutionStep] = []
    for index, command in enumerate(setup.selected, start=1):
        steps.append(
            _step_from_command(
                command,
                step_id=f"setup-{index:02d}",
                phase=ExecutionPhase.SETUP,
                timeout=active.step_timeout,
            )
        )
    for index, command in enumerate(services.selected, start=1):
        steps.append(
            _step_from_command(
                command,
                step_id=f"services-{index:02d}",
                phase=ExecutionPhase.SERVICES,
                timeout=active.step_timeout,
                expected="dependency services are started",
            )
        )
    for index, command in enumerate(migration.selected, start=1):
        steps.append(
            _step_from_command(
                command,
                step_id=f"migration-{index:02d}",
                phase=ExecutionPhase.MIGRATION,
                timeout=active.step_timeout,
                expected="database schema is up to date",
            )
        )

    launch_command = launch.selected[0]
    readiness = evidence_mod.build_readiness(profile, scan_contents, launch_candidates)
    launch_step = _step_from_command(
        launch_command,
        step_id="launch-01",
        phase=ExecutionPhase.LAUNCH,
        timeout=active.step_timeout,
        long_running=True,
        expected="the application starts and answers readiness checks",
    )
    launch_step = launch_step.model_copy(
        update={"readiness": readiness, "depends_on": [s.id for s in steps]}
    )
    steps.append(launch_step)

    verification_step = ExecutionStep(
        id="verify-01",
        phase=ExecutionPhase.VERIFICATION,
        purpose="verify readiness",
        action=_CHILD_ACTIONS[readiness.kind],
        command="",
        argv=[],
        cwd=".",
        evidence=list(readiness.evidence),
        risk=RiskLevel.SAFE,
        timeout_seconds=max(1.0, min(active.step_timeout, 600.0)),
        expected_outcome=f"{readiness.kind.value} readiness succeeds ({readiness.confidence.value})",
        depends_on=["launch-01"],
        readiness=readiness,
    )
    steps.append(verification_step)
    steps.append(
        ExecutionStep(
            id="cleanup-01",
            phase=ExecutionPhase.CLEANUP,
            purpose="stop started processes",
            action=StepAction.TERMINATE,
            command="",
            argv=[],
            risk=RiskLevel.SAFE,
            expected_outcome="no processes started by SkillForge remain",
            depends_on=["launch-01"],
        )
    )

    for selection, label in (
        (setup, "setup"),
        (services, "service"),
        (migration, "migration"),
        (launch, "launch"),
    ):
        for shell_command in selection.excluded_shell:
            notes.append(
                f"{label}: command uses shell syntax and was not scheduled: {shell_command}"
            )
        for review_command in selection.skipped_review:
            notes.append(
                f"{label}: REVIEW_REQUIRED command skipped by --only-safe: {review_command}"
            )
    if migration.skipped_review and not migration.selected:
        notes.append(
            "Migration commands exist but are REVIEW_REQUIRED; re-run without --only-safe "
            "to schedule and explicitly approve them."
        )
    if not readiness.evidence and readiness.kind is ReadinessKind.LOG_SIGNAL:
        notes.append(
            "No endpoint evidence was found; readiness falls back to a startup log signal."
        )

    workflows = [step for step in steps if step.action is StepAction.COMMAND]
    fingerprint = repository_fingerprint(
        Path(profile.root_path),
        workflows,
        git_commit=profile.git.commit,
        git_dirty=profile.git.dirty,
    )
    plan_id = (
        "plan-"
        + hashlib.sha256(
            f"{target}\n{profile.name}\n{workflow_fingerprint(workflows)}".encode()
        ).hexdigest()[:12]
    )
    return ExecutionPlan(
        id=plan_id,
        target=target,
        repository=profile.name,
        root_path=profile.root_path,
        created_at=datetime.now(UTC),
        steps=steps,
        risk_summary=_risk_summary(steps),
        excluded_dangerous=dangerous,
        fingerprint=fingerprint,
        notes=notes,
    )


__all__ = ["PlanOptions", "build_execution_plan"]
