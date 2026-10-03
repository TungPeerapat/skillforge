"""Rich rendering helpers for the CLI."""

from __future__ import annotations

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from skillforge.models import (
    FindingSeverity,
    RepositoryProfile,
    SkillPlan,
    ValidationResult,
)

_RISK_STYLES = {
    "SAFE": "green",
    "REVIEW_REQUIRED": "yellow",
    "DANGEROUS": "bold red",
}


_CI_TECHNOLOGY_NAMES = frozenset(
    {
        "GitHub Actions",
        "GitLab CI",
        "Jenkins",
        "Azure Pipelines",
        "CircleCI",
        "Travis CI",
        "Buildkite",
        "Woodpecker",
    }
)


def tech_summary(profile: RepositoryProfile, console: Console | None = None) -> Table:
    """Stack table: languages, frameworks, databases, CI."""
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column()

    def add(label: str, values: list[str]) -> None:
        if values:
            table.add_row(label, ", ".join(values))

    add("Languages", [stat.language for stat in profile.stats.languages[:5] if stat.lines])
    add(
        "Frameworks",
        [tech.display for tech in profile.technologies if tech.kind.value == "framework"][:6],
    )
    add("Databases", [database.display for database in profile.databases][:4])
    add(
        "CI/CD",
        sorted({tech.name for tech in profile.technologies if tech.name in _CI_TECHNOLOGY_NAMES}),
    )
    add(
        "Packages",
        [tech.name for tech in profile.technologies if tech.kind.value == "package_manager"],
    )
    add(
        "Containers",
        [
            tech.name
            for tech in profile.technologies
            if tech.kind.value in {"container", "orchestration"}
        ],
    )
    add("Environment", [tech.name for tech in profile.technologies if tech.kind.value == "config"])
    return table


def profile_panel(profile: RepositoryProfile) -> Panel:
    """Repository summary panel used by ``analyze``."""
    stats = profile.stats
    lines: list[Text] = [
        Text(f"{profile.name}", style="bold"),
        Text(
            f"{stats.total_files} files · {stats.total_lines:,} lines · "
            f"{stats.total_bytes // 1024:,} KiB · {stats.analyzed_files} read",
            style="dim",
        ),
        Text(profile.git.summary, style="dim"),
    ]
    return Panel(Group(*lines), title="Repository", border_style="cyan")


def workflow_table(profile: RepositoryProfile) -> Table:
    table = Table(title="Detected workflows", title_justify="left", box=None)
    table.add_column("Workflow", style="bold")
    table.add_column("Commands")
    table.add_column("Confidence", justify="right")
    for workflow in profile.workflows:
        commands = "\n".join(command.command for command in workflow.commands[:4])
        if len(workflow.commands) > 4:
            commands += f"\n… {len(workflow.commands) - 4} more"
        table.add_row(workflow.name, commands, f"{workflow.confidence:.2f}")
    if not profile.workflows:
        table.add_row("—", "no evidence-backed workflows found", "")
    return table


def plan_table(plan: SkillPlan) -> Table:
    table = Table(title="Recommended skills", title_justify="left", box=None)
    table.add_column("Skill", style="bold cyan", no_wrap=True)
    table.add_column("Why")
    table.add_column("Conf.", justify="right", no_wrap=True)
    for candidate in plan.candidates:
        table.add_row(candidate.name, candidate.reason, f"{candidate.confidence:.2f}")
    if not plan.candidates:
        table.add_row("—", "no skills recommended (not enough evidence)", "")
    return table


def validation_table(result: ValidationResult) -> Table:
    table = Table(title=f"Validation: {result.target}", title_justify="left", box=None)
    table.add_column("Level", no_wrap=True)
    table.add_column("Code", no_wrap=True, style="dim")
    table.add_column("Message")
    table.add_column("Location", style="dim")
    severity_style = {
        FindingSeverity.ERROR: "bold red",
        FindingSeverity.WARNING: "yellow",
        FindingSeverity.INFO: "dim",
    }
    for finding in result.sorted_findings():
        table.add_row(
            Text(finding.severity.label, style=severity_style[finding.severity]),
            finding.code,
            finding.message,
            finding.location,
        )
    if not result.findings:
        table.add_row("[green]PASS[/green]", "", "no findings", "")
    return table


def risk_table(profile: RepositoryProfile, *, minimum: str = "medium") -> Table | None:
    risks = profile.top_risks(minimum=minimum)
    if not risks:
        return None
    table = Table(title="Risks to keep in mind", title_justify="left", box=None)
    table.add_column("Severity", no_wrap=True)
    table.add_column("Risk")
    table.add_column("Evidence", style="dim")
    styles = {"critical": "bold red", "high": "red", "medium": "yellow", "low": "dim"}
    for risk in risks:
        evidence = risk.evidence[0].label if risk.evidence else "-"
        table.add_row(
            Text(risk.severity.value.upper(), style=styles.get(risk.severity.value, "")),
            risk.title,
            evidence,
        )
    return table


def unknowns_table(profile: RepositoryProfile) -> Table | None:
    if not profile.unknowns:
        return None
    table = Table(title="Open questions (not presented as fact)", title_justify="left", box=None)
    table.add_column("Question")
    table.add_column("How to check", style="dim")
    for unknown in profile.unknowns[:8]:
        table.add_row(unknown.question, unknown.suggested_check)
    return table


def command_risk_text(label: str) -> Text:
    return Text(label, style=_RISK_STYLES.get(label, ""))
