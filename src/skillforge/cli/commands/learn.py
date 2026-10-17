"""``skillforge learn`` — controlled execution, verification, and skill upgrade."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.table import Table
from rich.text import Text

from skillforge.analyzer import analyze_repository
from skillforge.cli.common import emit_json, get_state, handle_errors
from skillforge.cli.state import AppState
from skillforge.config import Settings
from skillforge.errors import NotFoundError, UsageError
from skillforge.execution import LocalExecutionBackend
from skillforge.learning import (
    DryRunSession,
    LearningSessionRunner,
    PlanOptions,
    new_session_id,
    save_session,
)
from skillforge.learning.llm_diagnosis import diagnose_failure
from skillforge.learning.targets import get_target, looks_like_target
from skillforge.logging import trace_span
from skillforge.models import (
    DEFAULT_OUTPUT_LIMIT_BYTES,
    Diagnosis,
    DiagnosisCategory,
    ExecutionAttempt,
    ExecutionStep,
    LearningSession,
    RepositoryProfile,
    SessionStatus,
    StepAction,
    ValidationResult,
)
from skillforge.providers import LLMProvider, resolve_provider
from skillforge.validator import SkillValidator

_RISK_STYLES = {
    "SAFE": "green",
    "REVIEW_REQUIRED": "yellow",
    "DANGEROUS": "bold red",
}


def register(app: typer.Typer) -> None:
    @app.command("learn")
    @handle_errors
    def learn(
        ctx: typer.Context,
        target: str = typer.Argument(
            "project-runner",
            help="Learning target (v0.2 supports: project-runner).",
        ),
        path: Path | None = typer.Argument(
            None,
            help="Repository to learn (defaults to the current directory).",
        ),
        dry_run: bool = typer.Option(
            False, "--dry-run", help="Plan and classify only; execute nothing, write nothing."
        ),
        yes: bool = typer.Option(
            False, "--yes", "-y", help="Explicit approval for non-interactive execution."
        ),
        sandbox: str = typer.Option(
            "local",
            "--sandbox",
            help="Execution backend: local (v0.2). Docker isolation is not implemented yet.",
        ),
        step_timeout: float = typer.Option(
            120.0, "--step-timeout", min=1.0, max=3600.0, help="Per-command timeout in seconds."
        ),
        ready_timeout: float = typer.Option(
            30.0, "--ready-timeout", min=1.0, max=600.0, help="Readiness wait budget in seconds."
        ),
        max_attempts: int = typer.Option(
            3,
            "--max-attempts",
            min=1,
            max=5,
            help="Maximum attempts per step (including the first).",
        ),
        only_safe: bool = typer.Option(
            False, "--only-safe", help="Exclude REVIEW_REQUIRED commands from the plan."
        ),
        provider: str | None = typer.Option(
            None,
            "--provider",
            help="Optional LLM provider for failure diagnosis (none | mock | openai | anthropic | openrouter).",
        ),
        allow_external_llm: bool = typer.Option(
            False,
            "--allow-external-llm",
            help="Consent to sending redacted failure context to a remote provider.",
        ),
        force: bool = typer.Option(
            False, "--force", help="Allow upgrading a skill that SkillForge did not generate."
        ),
        json_output: bool = typer.Option(False, "--json", help="Print a machine-readable session."),
    ) -> None:
        """Learn project-runner by executing the plan safely, then verify and upgrade."""
        state = get_state(ctx)
        state.json_mode = state.json_mode or json_output
        target_id, repo_arg = _resolve_target_and_path(target, path)
        learning_target = get_target(target_id)  # raises UsageError for unknown targets
        repo_root = repo_arg.expanduser().resolve()
        if not repo_root.is_dir():
            raise NotFoundError(
                f"Not a directory: {repo_root}",
                hint="pass the path of the repository you want to learn",
            )
        if sandbox.strip().lower() != "local":
            raise UsageError(
                f"--sandbox {sandbox} is not implemented in v0.2",
                hint="only 'local' execution is available; isolation is a documented limitation",
            )
        settings = state.settings_for(repo_root)

        analysis = analyze_repository(repo_root, settings)
        profile = analysis.profile
        plan = learning_target.build_plan(
            profile,
            scan_contents=analysis.scan.contents,
            options=PlanOptions(step_timeout=step_timeout, only_safe=only_safe),
        )
        session = LearningSession(
            id=new_session_id(),
            target=learning_target.id,
            repository=profile.name,
            root_path=str(repo_root),
            created_at=datetime.now(UTC),
            plan=plan,
        )

        if dry_run:
            DryRunSession.mark(session)
            _render_session(state, session, settings, profile)
            return

        if state.json_mode and not yes:
            raise UsageError(
                "--json requires --yes for execution (consent is never inferred)",
                hint="run interactively, or pass --yes to approve the displayed plan",
            )

        approval_mode = "yes" if yes else "interactive"
        if yes or state.json_mode:
            approved = True
        else:
            approved = _approve_plan(state, session)
        if not approved:
            session.status = SessionStatus.ABORTED
            session.completed_at = datetime.now(UTC)
            session.notes.append("the user declined approval; no commands were executed")
            if state.json_mode:
                emit_json(state, _session_payload(session, profile))
                return
            state.console.print(
                "\n[yellow]Aborted[/yellow]: no commands were executed and nothing was written."
            )
            return

        provider_instance = _resolve_provider(state, settings, provider, allow_external_llm)

        def retry_approval(diagnosis: Diagnosis, step: ExecutionStep, next_attempt: int) -> bool:
            if state.json_mode:  # pragma: no cover - json requires --yes
                return False
            return _approve_retry(state, diagnosis, step, next_attempt, max_attempts)

        backend = LocalExecutionBackend(
            repo_root, default_timeout=step_timeout, output_limit=DEFAULT_OUTPUT_LIMIT_BYTES
        )

        def provider_callback(
            active: LearningSession,
            attempt: ExecutionAttempt,
            step: ExecutionStep,
            diagnosis: Diagnosis,
        ) -> Diagnosis | None:
            if provider_instance is None:
                return None
            if diagnosis.category not in (
                DiagnosisCategory.UNKNOWN,
                DiagnosisCategory.NONZERO_EXIT,
            ):
                return None
            return diagnose_failure(provider_instance, active, attempt, step)

        runner = LearningSessionRunner(
            session,
            backend=backend,
            repo_root=repo_root,
            approved=True,
            approval_mode=approval_mode,
            max_attempts=max_attempts,
            ready_timeout=ready_timeout,
            retry_approval=retry_approval,
            provider_diagnosis=provider_callback if provider_instance else None,
        )
        with trace_span("learn", target=learning_target.id, repository=profile.name):
            session = runner.run()

        if session.status is SessionStatus.VERIFIED and session.learned_workflow is not None:
            with trace_span("learn.upgrade", skill=learning_target.skill_name):
                skill_dir = _ensure_skill(settings, profile, repo_root, learning_target.skill_name)
                upgrade = learning_target.upgrade(
                    skill_dir,
                    session=session,
                    workflow=session.learned_workflow,
                    force=force,
                )
                session.skill_path = upgrade.directory
            save_session(session, repo_root)
            validation = SkillValidator(profile=profile).validate_dir(skill_dir)
        else:
            save_session(session, repo_root)
            validation = None

        _render_session(state, session, settings, profile, validation=validation)

        if validation is not None and not validation.ok:
            raise typer.Exit(1)
        if session.status in (SessionStatus.FAILED, SessionStatus.ABORTED):
            raise typer.Exit(1)


# ------------------------------------------------------------------ helpers


def _ensure_skill(
    settings: Settings, profile: RepositoryProfile, repo_root: Path, skill_name: str
) -> Path:
    """Generate the base skill first when learning has nothing to upgrade."""
    skill_dir = settings.output_dir(repo_root) / skill_name
    if (skill_dir / "SKILL.md").is_file():
        return skill_dir
    from skillforge.generator import SkillGenerator
    from skillforge.planner import SkillPlanner
    from skillforge.skills import write_generated_skill

    plan = SkillPlanner().plan(profile)
    generated = SkillGenerator(settings).generate(profile, names=[skill_name], plan=plan)
    return write_generated_skill(
        generated[0],
        settings.output_dir(repo_root),
        repository=profile.name,
        git_commit=profile.git.commit,
        git_dirty=profile.git.dirty,
        overwrite=False,
    )


def _resolve_target_and_path(target: str, path: Path | None) -> tuple[str, Path]:
    """Support ``learn project-runner <path>`` and ``learn <path>``."""
    if path is not None:
        return target.strip().lower(), path
    if looks_like_target(target):
        return target.strip().lower(), Path(".")
    candidate = Path(target).expanduser()
    if candidate.is_dir() or "/" in target or "\\" in target or Path(target).is_absolute():
        return "project-runner", candidate
    return target.strip().lower(), Path(".")


def _resolve_provider(
    state: AppState, settings: Settings, provider: str | None, allow_external: bool
) -> LLMProvider | None:
    requested = (provider or settings.provider.default or "none").strip().lower()
    if requested in ("", "none"):
        return None
    return resolve_provider(
        settings, explicit=requested, allow_external=allow_external or state.allow_external_llm
    )


def _plan_table(session: LearningSession) -> Table:
    table = Table(title="Execution plan", title_justify="left", box=None)
    table.add_column("#", justify="right", style="dim", no_wrap=True)
    table.add_column("Phase", no_wrap=True)
    table.add_column("Command / check")
    table.add_column("Risk", no_wrap=True)
    table.add_column("Evidence", style="dim")
    index = 0
    for step in session.plan.steps:
        if step.action is not StepAction.COMMAND:
            continue
        index += 1
        table.add_row(
            str(index),
            step.phase.label,
            step.command,
            Text(step.risk.label, style=_RISK_STYLES.get(step.risk.label, "")),
            step.provenance,
        )
    if index == 0:
        table.add_row("—", "—", "no executable commands", "", "")
    return table


def _verification_line(session: LearningSession) -> str:
    readiness = None
    for step in session.plan.steps:
        if step.readiness is not None:
            readiness = step.readiness
            break
    if readiness is None:
        return "no readiness check could be planned"
    if readiness.url:
        return (
            f"HTTP GET {readiness.url} → expect {readiness.expect_status} "
            f"({readiness.confidence.value}; {readiness.detail})"
        )
    if readiness.port:
        return (
            f"TCP 127.0.0.1:{readiness.port} → expect a connection "
            f"({readiness.confidence.value}; {readiness.detail})"
        )
    return f"{readiness.kind.value} ({readiness.confidence.value}; {readiness.detail})"


def _confirm(prompt: str, *, default: bool = False) -> bool:
    """A confirmation that treats EOF/Ctrl+D as the default (never inferred)."""
    try:
        return typer.confirm(prompt, default=default)
    except (typer.Abort, EOFError):
        return default


def _approve_plan(state: AppState, session: LearningSession) -> bool:
    console = state.console
    _print_plan(console, session)
    console.print(
        f"\nUp to [bold]{session.plan.risk_summary.get('REVIEW_REQUIRED', 0)}[/bold] "
        "REVIEW_REQUIRED command(s) may change local state. Destructive commands are blocked."
    )
    return _confirm("Continue?", default=False)


def _print_plan(console, session: LearningSession) -> None:  # type: ignore[no-untyped-def]
    console.print()
    console.print("[bold]SkillForge Learning Session[/bold]")
    console.print()
    console.print("[bold]Target[/bold]")
    console.print(f"  {session.target}")
    console.print()
    console.print("[bold]Discovered workflow[/bold]")
    console.print()
    console.print(_plan_table(session))
    console.print()
    console.print("[bold]Verification[/bold]")
    console.print(f"  {_verification_line(session)}")
    summary = session.plan.risk_summary
    console.print()
    console.print("[bold]Risk summary[/bold]")
    console.print(
        f"  SAFE {summary.get('SAFE', 0)} · "
        f"REVIEW_REQUIRED {summary.get('REVIEW_REQUIRED', 0)} · "
        f"DANGEROUS {summary.get('DANGEROUS', 0)}"
    )
    if session.plan.excluded_dangerous:
        console.print()
        console.print("[bold red]Blocked (never executed)[/bold red]")
        for command in session.plan.excluded_dangerous:
            console.print(f"  [red]{command}[/red]")
    if session.plan.notes:
        console.print()
        for note in session.plan.notes:
            console.print(f"[dim]{note}[/dim]")


def _approve_retry(
    state: AppState,
    diagnosis: Diagnosis,
    step: ExecutionStep,
    next_attempt: int,
    max_attempts: int,
) -> bool:
    console = state.console
    console.print()
    console.print(f"[yellow]Step {step.id} failed[/yellow]: {diagnosis.summary}")
    if diagnosis.evidence_lines:
        for line in diagnosis.evidence_lines[:3]:
            console.print(f"  [dim]{line}[/dim]")
    console.print(f"[dim]{diagnosis.suggested_resolution}[/dim]")
    return _confirm(
        f"Retry '{step.command}' (attempt {next_attempt}/{max_attempts})?", default=False
    )


def _attempt_table(session: LearningSession) -> Table:
    table = Table(title="Attempts", title_justify="left", box=None)
    table.add_column("Step", no_wrap=True)
    table.add_column("#", justify="right")
    table.add_column("Status", no_wrap=True)
    table.add_column("Exit", justify="right")
    table.add_column("Duration", justify="right")
    table.add_column("Diagnosis", style="dim")
    for attempt in session.attempts:
        table.add_row(
            attempt.step_id,
            str(attempt.attempt_number),
            attempt.status.value,
            "—" if attempt.exit_code is None else str(attempt.exit_code),
            f"{attempt.duration_ms / 1000:.1f}s",
            attempt.diagnosis.summary[:60] if attempt.diagnosis else "",
        )
    return table


def _render_session(
    state: AppState,
    session: LearningSession,
    settings: Settings,
    profile: RepositoryProfile,
    *,
    validation: ValidationResult | None = None,
) -> None:
    if state.json_mode:
        emit_json(state, _session_payload(session, profile, validation=validation))
        return
    console = state.console
    _print_plan(console, session)
    if session.status is SessionStatus.DRY_RUN:
        console.print("\n[bold]No commands executed (--dry-run).[/bold]")
        return
    if session.status is SessionStatus.ABORTED:
        console.print("\n[yellow]Aborted[/yellow]: nothing was executed.")
        return
    if session.attempts:
        console.print()
        console.print(_attempt_table(session))
    if session.verification is not None:
        result = session.verification
        marker = "[green]✓[/green]" if result.success else "[red]✗[/red]"
        console.print(f"\n{marker} {result.detail}")
    if session.status is SessionStatus.VERIFIED and session.learned_workflow is not None:
        workflow = session.learned_workflow
        console.print("[green]✓ Workflow verified[/green]")
        console.print()
        console.print(f"[bold]{workflow.target}[/bold]")
        console.print("Status: [green]VERIFIED[/green]")
        if session.skill_path:
            console.print(f"Skill upgraded: {session.skill_path}")
        if validation is not None:
            console.print(
                f"Validation: {len(validation.errors)} error(s), "
                f"{len(validation.warnings)} warning(s)"
            )
        console.print(
            f"\n[bold]Next:[/bold] skillforge validate {session.root_path}\n"
            f"       skillforge export {session.root_path} --to all"
        )
    elif session.status is SessionStatus.FAILED:
        console.print("\n[red]Status: FAILED[/red]")
        console.print(f"Session saved: .skillforge/sessions/{session.id}.json")
    console.print(f"[dim]session: {session.id}[/dim]")


def _session_payload(
    session: LearningSession,
    profile: RepositoryProfile,
    *,
    validation: ValidationResult | None = None,
) -> dict:
    payload: dict = {
        "dry_run": session.status is SessionStatus.DRY_RUN,
        "repository": profile.name,
        "target": session.target,
        "session": session.model_dump(mode="json"),
        "risk_summary": session.plan.risk_summary,
        "verification": session.verification.model_dump(mode="json")
        if session.verification
        else None,
        "skill": {"path": session.skill_path, "status": _skill_status(session)},
    }
    if validation is not None:
        payload["validation"] = {
            "errors": len(validation.errors),
            "warnings": len(validation.warnings),
        }
    return payload


def _skill_status(session: LearningSession) -> str:
    if session.status is SessionStatus.VERIFIED and session.learned_workflow is not None:
        return "verified"
    if session.status is SessionStatus.FAILED:
        return "failed"
    return "unverified"


__all__ = ["register"]
