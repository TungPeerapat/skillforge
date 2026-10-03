"""``skillforge eval`` — run evaluation scenarios."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from skillforge.cli.common import emit_json, get_state, handle_errors
from skillforge.cli.state import resolve_repo_root
from skillforge.errors import EnvironmentError_
from skillforge.evaluation import (
    EvaluationRunner,
    RecordedAgentExecutor,
    load_scenarios,
    render_report,
)


def register(app: typer.Typer) -> None:
    @app.command("eval")
    @handle_errors
    def evaluate(
        ctx: typer.Context,
        path: Path = typer.Option(
            ".", "--repo", help="SkillForge repository root (fixtures and scenarios live here)."
        ),
        scenario: list[str] = typer.Option(
            None, "--scenario", help="Run only these scenario ids (repeatable)."
        ),
        scenarios_dir: Path | None = typer.Option(
            None, "--scenarios-dir", help="Scenario directory (default: benchmarks/scenarios)."
        ),
        recordings: Path | None = typer.Option(
            None,
            "--recordings",
            help="Directory with recorded agent runs; enables agent metrics.",
        ),
        json_output: bool = typer.Option(False, "--json", help="Print the report as JSON."),
        report: Path | None = typer.Option(
            None, "--report", help="Write the JSON report to this path."
        ),
    ) -> None:
        """Evaluate generated skills against fixtures and report honest metrics."""
        state = get_state(ctx)
        state.json_mode = state.json_mode or json_output
        repo_root = resolve_repo_root(path)
        directory = scenarios_dir or (repo_root / "benchmarks" / "scenarios")
        if not directory.is_dir():
            raise EnvironmentError_(
                f"Scenario directory not found: {directory}",
                hint="pass --scenarios-dir or run from the SkillForge repository",
            )
        selected = load_scenarios(directory)
        if scenario:
            wanted = set(scenario)
            selected = [item for item in selected if item.id in wanted]
            if not selected:
                raise EnvironmentError_("No scenarios matched: " + ", ".join(sorted(wanted)))

        executor = None
        recording_dir = recordings or (repo_root / "benchmarks" / "recorded")
        if recording_dir.is_dir():
            executor = RecordedAgentExecutor(recording_dir)
        runner = EvaluationRunner(repo_root=repo_root, scenarios=selected, executor=executor)
        result = runner.run()

        if report is not None:
            report_path = report.expanduser()
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")

        if state.json_mode:
            emit_json(state, json.loads(result.model_dump_json()))
            return

        console = state.console
        console.print()
        console.print(f"[bold]Evaluating[/bold] {len(selected)} scenario(s) from {directory}")
        render_report(result, console)
        console.print()
        console.print(
            f"[bold]Checks:[/bold] {result.checks_passed}/{result.checks_total} passed"
            + (f", [red]{result.checks_failed} failed[/red]" if result.checks_failed else "")
        )

        if result.checks_failed:
            raise typer.Exit(1)


__all__ = ["register"]
