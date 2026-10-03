"""Evaluation report rendering."""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from skillforge.models import NOT_MEASURED, EvaluationReport


def report_table(report: EvaluationReport) -> Table:
    """Per-scenario check results."""
    table = Table(title="Evaluation scenarios", title_justify="left")
    table.add_column("Scenario", style="bold cyan")
    table.add_column("Checks", justify="right")
    table.add_column("Result")
    table.add_column("Skills", justify="right")
    for scenario in report.scenarios:
        total = len([o for o in scenario.outcomes if o.measured])
        passed = len([o for o in scenario.outcomes if o.passed])
        failed = [o for o in scenario.outcomes if o.measured and o.passed is False]
        result = "[green]PASS[/green]" if not failed else f"[red]FAIL ({len(failed)})[/red]"
        table.add_row(scenario.scenario_id, f"{passed}/{total}", result, str(len(scenario.skills)))
    return table


def metrics_table(report: EvaluationReport) -> Table:
    """Deterministic metrics per scenario."""
    table = Table(title="Measured metrics", title_justify="left")
    table.add_column("Scenario", style="bold cyan")
    table.add_column("Skills")
    table.add_column("Commands")
    table.add_column("Provenance")
    table.add_column("Val. errors")
    table.add_column("Max SKILL.md tokens")
    for scenario in report.scenarios:
        values = {metric.name: metric for metric in scenario.metrics}
        table.add_row(
            scenario.scenario_id,
            values["skills_generated"].display() if "skills_generated" in values else "-",
            values["commands_documented"].display() if "commands_documented" in values else "-",
            values["commands_with_provenance"].display()
            if "commands_with_provenance" in values
            else "-",
            values["validation_errors"].display() if "validation_errors" in values else "-",
            values["max_skill_md_tokens"].display() if "max_skill_md_tokens" in values else "-",
        )
    return table


def comparison_table(report: EvaluationReport) -> Table:
    table = Table(title="Agent comparison (without skill vs with skill)", title_justify="left")
    table.add_column("Metric", style="bold cyan")
    table.add_column("Without skill")
    table.add_column("With skill")
    for row in report.comparisons:
        table.add_row(row.metric, row.without_skill.display(), row.with_skill.display())
    return table


def render_report(report: EvaluationReport, console: Console) -> None:
    console.print()
    console.print(report_table(report))
    console.print()
    console.print(metrics_table(report))
    console.print()
    console.print(comparison_table(report))
    console.print()
    for note in report.notes:
        console.print(f"[dim]{note}[/dim]")
    if any(
        metric.display() == NOT_MEASURED
        for row in report.comparisons
        for metric in (row.without_skill, row.with_skill)
    ):
        console.print(
            "\n[yellow]Agent metrics are NOT MEASURED: no agent executor or recording was "
            "provided. See benchmarks/README.md for how to record real runs.[/yellow]"
        )
