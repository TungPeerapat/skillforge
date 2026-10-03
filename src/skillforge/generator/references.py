"""Reference documents shipped inside generated skills.

Progressive disclosure: ``SKILL.md`` stays short, while provenance and full
command lists live in ``references/`` and load only when an agent needs them.
"""

from __future__ import annotations

from skillforge.generator.blueprint import CommandView, SkillBlueprint
from skillforge.models import SkillFile


def _fence(lines: list[str], language: str = "bash") -> str:
    body = "\n".join(lines)
    return f"```{language}\n{body}\n```"


def _command_lines(commands: tuple[CommandView, ...]) -> list[str]:
    lines: list[str] = []
    current_cwd: str | None = None
    for command in commands:
        if command.cwd not in (".", "") and command.cwd != current_cwd:
            lines.append(f"# in {command.cwd}")
            current_cwd = command.cwd
        lines.append(command.command)
    return lines


def commands_reference(blueprint: SkillBlueprint) -> SkillFile | None:
    """Every command this skill may use, grouped by purpose, with risk labels."""
    if not blueprint.commands:
        return None
    grouped: dict[str, list[CommandView]] = {}
    for command in blueprint.commands:
        grouped.setdefault(command.purpose, []).append(command)

    lines: list[str] = [
        "# Commands in this skill",
        "",
        "Every command below was discovered in the repository. `SOURCE` is the file the command "
        "came from; nothing here is invented or guessed.",
        "",
    ]
    for purpose in sorted(grouped):
        lines.append(f"## {purpose.replace('_', ' ').title()}")
        lines.append("")
        for command in grouped[purpose]:
            lines.append(f"- `{command.command}`")
            lines.append(f"  - source: `{command.provenance}` ({command.source})")
            lines.append(f"  - risk: {command.risk_label}")
            if command.cwd not in (".", ""):
                lines.append(f"  - run from: `{command.cwd}`")
            for reason in command.risk_reasons:
                lines.append(f"  - review because: {reason}")
            for note in command.notes:
                lines.append(f"  - note: {note}")
        lines.append("")

    lines.append("## Copy-paste block")
    lines.append("")
    lines.append(_fence(_command_lines(blueprint.commands)))
    lines.append("")
    if blueprint.excluded_dangerous:
        lines.append("## Deliberately excluded")
        lines.append("")
        lines.append(
            "These commands were found in the repository but match destructive patterns. "
            "They are not part of this skill; review them manually if you need them."
        )
        lines.append("")
        for destructive in blueprint.excluded_dangerous:
            lines.append(f"- `{destructive}`")
        lines.append("")
    return SkillFile(
        path="references/commands.md",
        content="\n".join(lines),
        description="All discovered commands with sources and risk levels.",
    )


def workflows_reference(blueprint: SkillBlueprint) -> SkillFile | None:
    """The repository's end-to-end workflows, for context."""
    workflows = [workflow for workflow in blueprint.workflows if workflow.steps]
    if not workflows:
        return None
    lines: list[str] = [
        "# Repository workflows",
        "",
        "These are the full workflows detected in this repository, including steps that belong "
        "to other skills.",
        "",
    ]
    for workflow in workflows:
        lines.append(f"## {workflow.name}")
        lines.append("")
        if workflow.description:
            lines.append(workflow.description)
            lines.append("")
        if workflow.prerequisites:
            lines.append("Prerequisites:")
            for prerequisite in workflow.prerequisites:
                lines.append(f"- {prerequisite}")
            lines.append("")
        lines.append(_fence([step.command for step in workflow.steps if step.risk_level < 3]))
        lines.append("")
        for note in workflow.notes:
            lines.append(f"> {note}")
        if workflow.notes:
            lines.append("")
    return SkillFile(
        path="references/workflows.md",
        content="\n".join(lines),
        description="Full repository workflows with prerequisites.",
    )


def evidence_reference(blueprint: SkillBlueprint) -> SkillFile:
    """Provenance for every claim the skill makes."""
    lines: list[str] = [
        "# Evidence and provenance",
        "",
        "This skill was generated deterministically from the files below. "
        "Claims marked `inference` are derived, not directly observed.",
        "",
        "## Why this skill exists",
        "",
        f"- {blueprint.candidate.reason}",
        f"- confidence: {blueprint.candidate.confidence:.2f} ({blueprint.candidate.certainty.value})",
        "",
    ]
    if blueprint.candidate.evidence:
        lines.append("| Source | Detail | Kind |")
        lines.append("| --- | --- | --- |")
        for evidence in blueprint.candidate.evidence:
            detail = evidence.detail or evidence.snippet or ""
            lines.append(
                f"| `{evidence.label}` | {detail.replace('|', '\\|')} | {evidence.kind.value} |"
            )
        lines.append("")

    if blueprint.commands:
        lines.append("## Command provenance")
        lines.append("")
        lines.append("| Command | Source | Detail | Kind |")
        lines.append("| --- | --- | --- | --- |")
        for command in blueprint.commands:
            for evidence in command.evidence:
                detail = evidence.detail or evidence.snippet or ""
                lines.append(
                    f"| `{command.command}` | `{evidence.label}` | "
                    f"{detail.replace('|', '\\|')} | {evidence.kind.value} |"
                )
        lines.append("")

    if blueprint.unknowns:
        lines.append("## Known unknowns")
        lines.append("")
        for unknown in blueprint.unknowns:
            lines.append(f"- **{unknown.question}**")
            if unknown.why:
                lines.append(f"  - Why unknown: {unknown.why}")
            if unknown.suggested_check:
                lines.append(f"  - How to check: {unknown.suggested_check}")
            lines.append(f"  - Analysis evidence: `{unknown.provenance}`")
        lines.append("")

    lines.extend(
        [
            "## How this file was generated",
            "",
            f"- tool: SkillForge {blueprint.tool_version}",
            "- mode: deterministic templates plus repository analysis",
            "- repository files are treated as untrusted input; snippets are redacted",
            "",
        ]
    )
    return SkillFile(
        path="references/evidence.md",
        content="\n".join(lines),
        description="Provenance for every command and claim in this skill.",
    )


def architecture_reference(blueprint: SkillBlueprint) -> SkillFile | None:
    """Stack, components, services, databases, APIs, and documentation."""
    lines: list[str] = [
        "# Repository architecture",
        "",
        f"{blueprint.repository_summary}",
        "",
    ]
    if blueprint.components:
        lines.append("## Components")
        lines.append("")
        lines.append("| Component | Path | Kind | Technologies |")
        lines.append("| --- | --- | --- | --- |")
        for component in blueprint.components:
            technologies = ", ".join(component.technologies) or "-"
            lines.append(
                f"| {component.name} | `{component.path}` | {component.kind} | {technologies} |"
            )
        lines.append("")
    if blueprint.services:
        lines.append("## Services")
        lines.append("")
        lines.append("| Service | Kind | Image | Ports | Origin |")
        lines.append("| --- | --- | --- | --- | --- |")
        for service in blueprint.services:
            lines.append(
                f"| {service.name} | {service.kind} | `{service.image or '-'}` | "
                f"{', '.join(service.ports) or '-'} | {service.origin} |"
            )
        lines.append("")
    if blueprint.databases:
        lines.append("## Databases")
        lines.append("")
        for database in blueprint.databases:
            lines.append(f"- {database.display}")
            if database.migration_tool:
                lines.append(f"  - migrations: {database.migration_tool}")
            if database.migration_dirs:
                lines.append(f"  - migration directories: {', '.join(database.migration_dirs)}")
            lines.append(f"  - evidence: `{database.provenance}`")
        lines.append("")
    if blueprint.apis:
        lines.append("## API surface")
        lines.append("")
        for api in blueprint.apis:
            label = api.framework or api.kind
            lines.append(f"- {label} ({api.kind})")
            if api.entrypoints:
                lines.append(
                    f"  - entry points: {', '.join(f'`{item}`' for item in api.entrypoints)}"
                )
            if api.spec_paths:
                lines.append(
                    f"  - specifications: {', '.join(f'`{item}`' for item in api.spec_paths)}"
                )
            if api.router_dirs:
                lines.append(
                    f"  - route directories: {', '.join(f'`{item}`' for item in api.router_dirs)}"
                )
            lines.append(f"  - evidence: `{api.provenance}`")
        lines.append("")
    if blueprint.docs:
        lines.append("## Documentation")
        lines.append("")
        for doc in blueprint.docs[:15]:
            lines.append(f"- `{doc.path}` — {doc.title or doc.kind}")
        lines.append("")
    return SkillFile(
        path="references/architecture.md",
        content="\n".join(lines),
        description="Components, services, databases, and API surface.",
    )


def environment_reference(blueprint: SkillBlueprint) -> SkillFile | None:
    """Environment variables the project expects, names only."""
    if not blueprint.environment_keys and not blueprint.environment_files:
        return None
    lines: list[str] = [
        "# Environment configuration",
        "",
        "Only variable **names** are listed — values are never read by SkillForge and must come "
        "from the user or a secret manager.",
        "",
    ]
    if blueprint.environment_files:
        lines.append("Template files:")
        for path in blueprint.environment_files:
            lines.append(f"- `{path}`")
        lines.append("")
    if blueprint.environment_keys:
        lines.append("Declared variables:")
        lines.append("")
        for key in blueprint.environment_keys:
            lines.append(f"- `{key}`")
        lines.append("")
    lines.extend(
        [
            "## Handling rules",
            "",
            "- Never commit real values; keep them in `.env` (git-ignored) or a secret manager.",
            "- Do not print values into logs, prompts, or issue trackers.",
            "- If a value is missing, ask the user rather than guessing a default.",
            "",
        ]
    )
    return SkillFile(
        path="references/environment.md",
        content="\n".join(lines),
        description="Environment variable names and handling rules.",
    )
