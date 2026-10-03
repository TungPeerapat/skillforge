"""``skillforge doctor`` command."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.table import Table
from rich.text import Text

from skillforge.cli.common import emit_json, get_state, handle_errors
from skillforge.cli.state import resolve_repo_root
from skillforge.doctor import inspect_environment
from skillforge.models import CheckStatus

_STATUS_STYLE = {
    CheckStatus.OK: "green",
    CheckStatus.WARN: "yellow",
    CheckStatus.FAIL: "bold red",
    CheckStatus.MISSING: "dim",
    CheckStatus.SKIPPED: "dim",
}


def register(app: typer.Typer) -> None:
    @app.command("doctor")
    @handle_errors
    def doctor(
        ctx: typer.Context,
        path: Path = typer.Argument(".", help="Repository to inspect (optional)."),
        json_output: bool = typer.Option(False, "--json", help="Print JSON."),
    ) -> None:
        """Inspect the environment and report what is available."""
        state = get_state(ctx)
        state.json_mode = state.json_mode or json_output
        repo_root = resolve_repo_root(path) if path.exists() else None

        report = inspect_environment(repo_root)

        if state.json_mode:
            emit_json(state, report.model_dump(mode="json"))
            return

        console = state.console
        console.print()
        console.print(f"[bold]SkillForge Doctor[/bold] {report.tool_version}")
        console.print(f"[dim]{report.platform} · Python {report.python_version}[/dim]")
        console.print()
        for category in report.categories:
            table = Table(title=category, title_justify="left", box=None)
            table.add_column("Check", style="bold")
            table.add_column("Status", no_wrap=True)
            table.add_column("Detail", style="dim")
            for check in report.by_category(category):
                style = _STATUS_STYLE.get(check.status, "")
                table.add_row(
                    check.name,
                    Text(f"{check.status.symbol} {check.status.value}", style=style),
                    check.display_value,
                )
            console.print(table)
            console.print()
        if report.config_source:
            console.print(f"[dim]configuration: {report.config_source}[/dim]")
        if report.skill_output:
            console.print(
                f"[dim]skills output: {report.skill_output} ({report.generated_skills} generated)[/dim]"
            )
        for issue in report.config_issues:
            console.print(f"[red]configuration problem: {issue}[/red]")
        if not report.ok:
            console.print(
                "\n[red]Some required tools are missing.[/red] Fix them before running the full workflow."
            )
        else:
            console.print("\n[green]Environment looks usable.[/green]")


__all__ = ["register"]
