"""Shared CLI plumbing: error handling, options, and analysis helpers."""

from __future__ import annotations

import functools
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import typer

from skillforge.analyzer import AnalysisResult, analyze_repository
from skillforge.cli.state import AppState, resolve_repo_root
from skillforge.config import Settings
from skillforge.errors import SkillForgeError
from skillforge.logging import reset_tracer, use_tracer
from skillforge.models import RepositoryProfile, SkillPlan
from skillforge.planner import SkillPlanner

F = TypeVar("F", bound=Callable[..., Any])

REPO_ARGUMENT = typer.Argument(
    Path("."),
    help="Repository to work on (defaults to the current directory).",
    exists=False,
)


def get_state(ctx: typer.Context) -> AppState:
    state = ctx.obj
    if not isinstance(state, AppState):  # pragma: no cover - programming error
        raise typer.Exit(2)
    return state


def effective_repo_root(state: AppState, path: Path) -> Path:
    return resolve_repo_root(path)


def emit_json(state: AppState, payload: dict[str, Any]) -> None:
    """Print a single JSON document on stdout (logs go to stderr).

    Written directly to stdout rather than through Rich so that terminal width
    can never introduce line breaks inside JSON strings.
    """
    import sys

    sys.stdout.write(json.dumps(payload, indent=2, default=str))
    sys.stdout.write("\n")
    sys.stdout.flush()


def handle_errors[F: Callable[..., Any]](func: F) -> F:
    """Map :class:`SkillForgeError` to exit codes and readable output."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        ctx = kwargs.get("ctx") or (
            args[0] if args and isinstance(args[0], typer.Context) else None
        )
        state = ctx.obj if ctx is not None and isinstance(ctx.obj, AppState) else None
        token = use_tracer(state.tracer) if state is not None else None
        try:
            return func(*args, **kwargs)
        except SkillForgeError as exc:
            if state is not None:
                state.print_error(exc.message, hint=exc.hint)
                raise typer.Exit(exc.exit_code) from exc
            typer.echo(f"error: {exc.message}", err=True)
            raise typer.Exit(exc.exit_code) from exc
        except KeyboardInterrupt:  # pragma: no cover - interactive
            if state is not None:
                state.console.print("[yellow]interrupted[/yellow]")
            raise typer.Exit(130) from None
        finally:
            if token is not None:
                reset_tracer(token)

    return wrapper  # type: ignore[return-value]


def run_analysis(
    repo_root: Path, settings: Settings, state: AppState
) -> tuple[AnalysisResult, SkillPlan]:
    """Analyze a repository and plan skills, recording a trace span."""
    result = analyze_repository(repo_root, settings)
    plan = SkillPlanner().plan(result.profile)
    return result, plan


def profile_or_die(result: AnalysisResult) -> RepositoryProfile:
    return result.profile
