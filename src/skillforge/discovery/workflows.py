"""Workflow synthesis from discovered evidence."""

from __future__ import annotations

from skillforge.models import (
    API,
    Certainty,
    Command,
    Database,
    ProjectComponent,
    Service,
    Workflow,
)
from skillforge.models.workflow import CommandPurpose, WorkflowCategory
from skillforge.utils.text import slugify

_PURPOSE_TO_CATEGORY: dict[CommandPurpose, WorkflowCategory] = {
    CommandPurpose.SETUP: WorkflowCategory.SETUP,
    CommandPurpose.RUN: WorkflowCategory.RUN,
    CommandPurpose.TEST: WorkflowCategory.TEST,
    CommandPurpose.BUILD: WorkflowCategory.BUILD,
    CommandPurpose.LINT: WorkflowCategory.LINT,
    CommandPurpose.FORMAT: WorkflowCategory.LINT,
    CommandPurpose.TYPECHECK: WorkflowCategory.LINT,
    CommandPurpose.MIGRATE: WorkflowCategory.MIGRATE,
    CommandPurpose.SEED: WorkflowCategory.MIGRATE,
    CommandPurpose.DEPLOY: WorkflowCategory.DEPLOY,
    CommandPurpose.RELEASE: WorkflowCategory.DEPLOY,
    CommandPurpose.VERIFY: WorkflowCategory.VERIFY,
    CommandPurpose.CLEAN: WorkflowCategory.OTHER,
    CommandPurpose.DOCS: WorkflowCategory.OTHER,
    CommandPurpose.OTHER: WorkflowCategory.OTHER,
}

_CATEGORY_ORDER: tuple[WorkflowCategory, ...] = (
    WorkflowCategory.SETUP,
    WorkflowCategory.RUN,
    WorkflowCategory.TEST,
    WorkflowCategory.BUILD,
    WorkflowCategory.LINT,
    WorkflowCategory.MIGRATE,
    WorkflowCategory.VERIFY,
    WorkflowCategory.DEPLOY,
    WorkflowCategory.OTHER,
)

#: Commands that are useful to know but should not be featured in a workflow.
_EXCLUDED_FOR_WORKFLOWS = frozenset({"clean", "docs"})


def _confidence(commands: list[Command]) -> float:
    if not commands:
        return 0.0
    best = max(command.confidence for command in commands)
    return round(min(0.98, best + 0.05 * (len(commands) - 1)), 3)


def synthesize_workflows(
    commands: list[Command],
    *,
    components: list[ProjectComponent],
    services: list[Service],
    databases: list[Database],
    apis: list[API],
    repo_name: str,
) -> list[Workflow]:
    """Group commands into named workflows, deterministically.

    A single component produces ``test``; a monorepo produces
    ``web-test`` / ``api-test``. No command is invented here: everything comes
    from the evidence-backed command list.
    """
    usable = [
        command
        for command in commands
        if command.certainty is not Certainty.UNKNOWN
        and command.purpose.value not in _EXCLUDED_FOR_WORKFLOWS
        and not command.placeholders
    ]
    by_category: dict[WorkflowCategory, list[Command]] = {}
    for command in usable:
        category = _PURPOSE_TO_CATEGORY.get(command.purpose, WorkflowCategory.OTHER)
        by_category.setdefault(category, []).append(command)

    repo_has_multiple_components = len(components) > 1
    workflows: list[Workflow] = []
    for category in _CATEGORY_ORDER:
        category_commands = by_category.get(category, [])
        if not category_commands:
            continue
        grouped: dict[str, list[Command]] = {}
        for command in category_commands:
            key = command.component or ""
            grouped.setdefault(key, []).append(command)
        # Workflow ids must be unique: qualify with the component when a category
        # has more than one group or the repository has multiple components.
        multi = repo_has_multiple_components or len(grouped) > 1
        for component_key, group in sorted(grouped.items()):
            workflow_id = _workflow_id(category, component_key, multi)
            name = _workflow_name(category, component_key, multi, repo_name)
            evidence = [item for command in group for item in command.evidence]
            workflows.append(
                Workflow(
                    id=workflow_id,
                    name=name,
                    category=category,
                    description=_describe(category, component_key, databases, apis),
                    component=component_key or None,
                    commands=sorted(group, key=lambda item: (item.command,)),
                    prerequisites=_prerequisites(category, group, services, databases),
                    evidence=evidence,
                    confidence=_confidence(group),
                    certainty=Certainty.FACT,
                    notes=_notes(category, group),
                )
            )
    return workflows


def _workflow_id(category: WorkflowCategory, component: str, multi: bool) -> str:
    if not multi or not component:
        return category.value
    return f"{slugify(component, max_length=32)}-{category.value}"


def _workflow_name(category: WorkflowCategory, component: str, multi: bool, repo_name: str) -> str:
    base = category.label
    if multi and component:
        return f"{component}: {base}"
    return base


def _describe(
    category: WorkflowCategory,
    component: str,
    databases: list[Database],
    apis: list[API],
) -> str:
    target = f" for {component}" if component else ""
    if category is WorkflowCategory.MIGRATE and databases:
        tools = sorted({db.migration_tool for db in databases if db.migration_tool})
        if tools:
            return f"Apply and inspect database migrations using {', '.join(tools)}{target}."
    if category is WorkflowCategory.VERIFY and apis:
        return f"Verify the API surface{target}."
    return {
        WorkflowCategory.SETUP: f"Install dependencies and prepare the development environment{target}.",
        WorkflowCategory.RUN: f"Start the application or development server{target}.",
        WorkflowCategory.TEST: f"Run the automated test suite{target}.",
        WorkflowCategory.BUILD: f"Produce a production build or compiled artifact{target}.",
        WorkflowCategory.LINT: f"Run linters, formatters, and type checkers{target}.",
        WorkflowCategory.DEPLOY: f"Release or deploy the project{target}.",
        WorkflowCategory.OTHER: f"Repository tasks{target}.",
    }.get(category, f"Repository tasks{target}.")


def _prerequisites(
    category: WorkflowCategory,
    commands: list[Command],
    services: list[Service],
    databases: list[Database],
) -> list[str]:
    prerequisites: list[str] = []
    local_services = [service for service in services if service.is_local_dependency]
    if category in (WorkflowCategory.TEST, WorkflowCategory.MIGRATE, WorkflowCategory.RUN):
        if local_services:
            names = ", ".join(sorted(service.name for service in local_services))
            prerequisites.append(f"Services from docker compose must be available: {names}")
        if category is WorkflowCategory.MIGRATE and databases:
            prerequisites.append("A reachable database and a configured connection string")
    if any("sudo" in command.risk_reasons for command in commands):
        prerequisites.append("Elevated permissions required for some commands")
    return prerequisites


def _notes(category: WorkflowCategory, commands: list[Command]) -> list[str]:
    notes: list[str] = []
    risky = [command for command in commands if not command.risk.is_safe]
    if risky:
        notes.append(
            f"{len(risky)} command(s) in this workflow require review before execution "
            f"(highest risk: {max(command.risk for command in risky).label})"
        )
    if category is WorkflowCategory.MIGRATE:
        notes.append("Apply migrations only against a database you are allowed to modify")
    return notes
