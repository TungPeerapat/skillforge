"""Planning context and shared helpers.

The context gives rules a small, typed vocabulary for asking questions about a
:class:`~skillforge.models.repository.RepositoryProfile`. Rules never mutate the
profile and never perform I/O, which keeps planning deterministic and testable.
"""

from __future__ import annotations

from dataclasses import dataclass

from skillforge.models import (
    API,
    Command,
    Database,
    Evidence,
    RepositoryProfile,
    Service,
    Workflow,
)
from skillforge.models.workflow import CommandPurpose, WorkflowCategory

_FLOW_PURPOSES: dict[WorkflowCategory, tuple[CommandPurpose, ...]] = {
    WorkflowCategory.SETUP: (CommandPurpose.SETUP,),
    WorkflowCategory.RUN: (CommandPurpose.RUN,),
    WorkflowCategory.TEST: (CommandPurpose.TEST,),
    WorkflowCategory.BUILD: (CommandPurpose.BUILD,),
    WorkflowCategory.LINT: (CommandPurpose.LINT, CommandPurpose.FORMAT, CommandPurpose.TYPECHECK),
    WorkflowCategory.MIGRATE: (CommandPurpose.MIGRATE, CommandPurpose.SEED),
    WorkflowCategory.VERIFY: (CommandPurpose.VERIFY,),
    WorkflowCategory.DEPLOY: (CommandPurpose.DEPLOY, CommandPurpose.RELEASE),
}


@dataclass(frozen=True)
class PlanContext:
    """Read-only view of the profile used by planning rules."""

    profile: RepositoryProfile

    # ------------------------------------------------------------------ basics
    def has_technology(self, name: str) -> bool:
        return self.profile.has_technology(name)

    @property
    def technologies(self) -> list[str]:
        return self.profile.technology_names()

    def commands(self, purpose: CommandPurpose) -> list[Command]:
        return self.profile.commands_for_purpose(purpose.value)

    def commands_of(self, *purposes: CommandPurpose) -> list[Command]:
        wanted = {purpose.value for purpose in purposes}
        return [command for command in self.profile.commands if command.purpose.value in wanted]

    def workflow(self, category: WorkflowCategory) -> Workflow | None:
        workflows = self.profile.workflows_by_category(category.value)
        if not workflows:
            return None
        # Prefer a repo-level workflow over a component-specific one.
        return min(workflows, key=lambda workflow: (workflow.component is not None, workflow.id))

    def workflows(self, category: WorkflowCategory) -> list[Workflow]:
        return self.profile.workflows_by_category(category.value)

    def workflow_commands(self, category: WorkflowCategory) -> list[Command]:
        commands: list[Command] = []
        for workflow in self.workflows(category):
            commands.extend(workflow.commands)
        return commands

    # ----------------------------------------------------------------- objects
    @property
    def databases(self) -> list[Database]:
        return [
            database
            for database in self.profile.databases
            if database.engine.value != "unknown" or database.migration_tool
        ]

    def databases_with_migrations(self) -> list[Database]:
        return [database for database in self.profile.databases if database.migration_tool]

    @property
    def apis(self) -> list[API]:
        return self.profile.apis

    @property
    def services(self) -> list[Service]:
        return [
            service
            for service in self.profile.services
            if service.is_local_dependency
            and service.kind.value in {"database", "cache", "queue", "storage"}
        ]

    def docs(self, kind: str | None = None) -> list:
        if kind is None:
            return list(self.profile.docs)
        return [doc for doc in self.profile.docs if doc.kind.value == kind]

    @property
    def risks(self) -> list[str]:
        return [
            risk.title for risk in self.profile.risks if risk.severity.value in {"high", "critical"}
        ]

    # ---------------------------------------------------------------- evidence
    def evidence_for_commands(self, commands: list[Command], limit: int = 4) -> list[Evidence]:
        evidence: list[Evidence] = []
        for command in commands:
            evidence.extend(command.evidence)
            if len(evidence) >= limit:
                break
        return evidence[:limit]

    def technology_version(self, name: str) -> str | None:
        lowered = name.lower()
        for technology in self.profile.technologies:
            if technology.name.lower() == lowered:
                return technology.version
        return None

    def primary_language(self) -> str | None:
        languages = self.profile.primary_languages(limit=1)
        return languages[0] if languages else None
