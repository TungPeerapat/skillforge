"""``skillforge analyze`` — deterministic repository analysis."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from skillforge.analyzer import analyze_repository
from skillforge.cli.common import REPO_ARGUMENT, emit_json, get_state, handle_errors
from skillforge.cli.ui import (
    plan_table,
    profile_panel,
    risk_table,
    tech_summary,
    unknowns_table,
    workflow_table,
)
from skillforge.errors import NotFoundError
from skillforge.planner import SkillPlanner
from skillforge.utils.fs import ensure_directory

DEFAULT_REPORT = Path(".skillforge/analysis.json")


def register(app: typer.Typer) -> None:
    @app.command("analyze")
    @handle_errors
    def analyze(
        ctx: typer.Context,
        path: Path = REPO_ARGUMENT,
        json_output: bool = typer.Option(False, "--json", help="Print the full analysis as JSON."),
        save: bool = typer.Option(
            False, "--save", help="Write the analysis report to .skillforge/analysis.json."
        ),
        out: Path | None = typer.Option(
            None, "--out", help="Report path for --save (default: .skillforge/analysis.json)."
        ),
        no_plan: bool = typer.Option(False, "--no-plan", help="Skip skill planning."),
    ) -> None:
        """Analyse a repository and recommend skills."""
        state = get_state(ctx)
        state.json_mode = state.json_mode or json_output
        repo_root = path.expanduser().resolve()
        if not repo_root.is_dir():
            raise NotFoundError(
                f"Not a directory: {repo_root}",
                hint="pass the path of the repository you want to work on",
            )
        settings = state.settings_for(repo_root)

        result = analyze_repository(repo_root, settings)
        profile = result.profile
        plan = None if no_plan else SkillPlanner().plan(profile)

        if state.json_mode:
            payload: dict = {
                "tool_version": profile.tool_version,
                "repository": {
                    "name": profile.name,
                    "path": str(profile.root_path),
                    "git": profile.git.model_dump(),
                },
                "profile": profile.model_dump(mode="json"),
                "timings_ms": state.tracer.total_ms(),
            }
            if plan is not None:
                payload["plan"] = json.loads(plan.model_dump_json())
            emit_json(state, payload)
        else:
            console = state.console
            console.print()
            console.print(f"[bold]Analyzing[/bold] {repo_root}")
            console.print()
            console.print(profile_panel(profile))
            console.print(tech_summary(profile))
            console.print()
            console.print(
                f"[bold]{profile.stats.total_files}[/bold] files · "
                f"[bold]{profile.stats.total_lines:,}[/bold] lines · "
                f"[bold]{len(profile.commands)}[/bold] evidenced commands · "
                f"[bold]{len(profile.workflows)}[/bold] workflows"
            )
            console.print()
            console.print(workflow_table(profile))
            console.print()
            if plan is not None:
                console.print(plan_table(plan))
                console.print()
            risks = risk_table(profile)
            if risks:
                console.print(risks)
                console.print()
            unknowns = unknowns_table(profile)
            if unknowns:
                console.print(unknowns)
                console.print()
            if plan is not None and plan.candidates:
                names = " ".join(candidate.name for candidate in plan.candidates)
                console.print(
                    f"[bold]Next:[/bold] skillforge generate {path}   [dim](or: {names})[/dim]"
                )
            else:
                console.print("[yellow]No skills were recommended for this repository.[/yellow]")

        if save:
            report_path = (out or (repo_root / DEFAULT_REPORT)).expanduser()
            ensure_directory(report_path.parent)
            payload = {
                "tool_version": profile.tool_version,
                "profile": profile.model_dump(mode="json"),
                "plan": json.loads(plan.model_dump_json()) if plan is not None else None,
            }
            report_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            if not state.json_mode:
                state.console.print(f"[dim]Report written to {report_path}[/dim]")


__all__ = ["register"]
