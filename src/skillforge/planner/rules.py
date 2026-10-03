"""Planning rules: one function per supported skill type.

Every rule answers the same questions:

* Is there *evidence* in this repository that the skill is useful?
* What concrete commands or files justify it?
* What must exist first (dependencies), and what risks should the agent know?

Rules return ``None`` when the repository does not justify the skill. Nothing is
recommended "because most projects have one".
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from skillforge.models import (
    Certainty,
    Command,
    Evidence,
    SkillCandidate,
    confidence_from_evidence,
)
from skillforge.models.workflow import CommandPurpose, WorkflowCategory
from skillforge.planner.context import PlanContext

#: Minimum confidence for a candidate to be recommended.
MIN_CONFIDENCE = 0.35


@dataclass(frozen=True)
class PlanRule:
    """A named planner rule."""

    id: str
    skill: str
    title: str
    priority: int
    evaluate: Callable[[PlanContext], SkillCandidate | None]
    tags: tuple[str, ...] = field(default_factory=tuple)

    def run(self, context: PlanContext) -> SkillCandidate | None:
        candidate = self.evaluate(context)
        if candidate is None:
            return None
        if candidate.confidence < MIN_CONFIDENCE:
            return None
        return candidate


def _candidate(
    rule: PlanRule,
    *,
    reason: str,
    evidence: list[Evidence],
    dependencies: list[str] | None = None,
    requires: list[str] | None = None,
    risks: list[str] | None = None,
    notes: list[str] | None = None,
    certainty: Certainty = Certainty.INFERENCE,
    confidence: float | None = None,
) -> SkillCandidate:
    return SkillCandidate(
        name=rule.skill,
        title=rule.title,
        reason=reason,
        evidence=evidence,
        confidence=confidence if confidence is not None else confidence_from_evidence(evidence),
        certainty=certainty,
        dependencies=dependencies or [],
        requires=requires or [],
        risks=risks or [],
        priority=rule.priority,
        tags=list(rule.tags),
        notes=notes or [],
    )


def _first(commands: list[Command]) -> Command:
    return commands[0]


def _command_line(command: Command) -> str:
    return f"`{command.command}` ({command.provenance})"


def _review_note(commands: list[Command]) -> list[str]:
    risky = [command for command in commands if not command.risk.is_safe]
    if not risky:
        return []
    return [
        f"{len(risky)} command(s) require review before execution "
        f"(highest risk: {max(command.risk for command in risky).label})"
    ]


# --------------------------------------------------------------------- runner


def evaluate_project_runner(context: PlanContext) -> SkillCandidate | None:
    commands = context.commands(CommandPurpose.RUN)
    if not commands:
        return None
    primary = _first(commands)
    reason = f"The application can be started with {_command_line(primary)}."
    services = context.services
    notes: list[str] = []
    if services:
        notes.append(
            "Dependency services must be available first: "
            + ", ".join(sorted({service.name for service in services}))
        )
    return _candidate(
        PROJECT_RUNNER_RULE,
        reason=reason,
        evidence=context.evidence_for_commands(commands),
        requires=[context.primary_language() or "runtime"],
        risks=_review_note(commands),
        notes=notes,
        certainty=primary.certainty,
    )


PROJECT_RUNNER_RULE = PlanRule(
    id="project-runner-rule",
    skill="project-runner",
    title="Project runner",
    priority=10,
    evaluate=evaluate_project_runner,
    tags=("runtime",),
)


# --------------------------------------------------------------------- builder


def evaluate_project_builder(context: PlanContext) -> SkillCandidate | None:
    commands = context.workflow_commands(WorkflowCategory.BUILD)
    if not commands:
        return None
    primary = _first(commands)
    reason = f"A production build is available via {_command_line(primary)}."
    return _candidate(
        PROJECT_BUILDER_RULE,
        reason=reason,
        evidence=context.evidence_for_commands(commands),
        requires=[context.primary_language() or "runtime"],
        risks=_review_note(commands),
        certainty=primary.certainty,
    )


PROJECT_BUILDER_RULE = PlanRule(
    id="project-builder-rule",
    skill="project-builder",
    title="Project builder",
    priority=20,
    evaluate=evaluate_project_builder,
    tags=("build",),
)


# ----------------------------------------------------------------- test runner


def evaluate_test_runner(context: PlanContext) -> SkillCandidate | None:
    commands = context.workflow_commands(WorkflowCategory.TEST)
    if not commands:
        return None
    frameworks = [
        technology.name
        for technology in context.profile.technologies
        if technology.kind.value == "test_framework"
    ]
    primary = _first(commands)
    detail = f" using {', '.join(sorted(set(frameworks)))}" if frameworks else ""
    reason = f"The test suite runs with {_command_line(primary)}{detail}."
    return _candidate(
        TEST_RUNNER_RULE,
        reason=reason,
        evidence=context.evidence_for_commands(commands),
        requires=[context.primary_language() or "runtime"],
        certainty=primary.certainty,
    )


TEST_RUNNER_RULE = PlanRule(
    id="test-runner-rule",
    skill="test-runner",
    title="Test runner",
    priority=15,
    evaluate=evaluate_test_runner,
    tags=("testing",),
)


# -------------------------------------------------------------------- debugger


def evaluate_project_debugger(context: PlanContext) -> SkillCandidate | None:
    run_commands = context.commands(CommandPurpose.RUN)
    if not run_commands:
        return None
    services = context.services
    apis = context.apis
    signals: list[str] = []
    evidence = context.evidence_for_commands(run_commands)
    if services:
        signals.append(
            "dependency services ("
            + ", ".join(sorted({service.name for service in services}))
            + ")"
        )
        for service in services:
            evidence.extend(service.evidence)
    if apis:
        signals.append("an HTTP API surface")
        for api in apis:
            evidence.extend(api.evidence)
    if not signals:
        # A run command alone is not enough to justify a debugging skill.
        return None
    reason = (
        "Debugging requires the run command plus "
        + " and ".join(signals)
        + f"; start with {_command_line(_first(run_commands))}."
    )
    return _candidate(
        PROJECT_DEBUGGER_RULE,
        reason=reason,
        evidence=evidence[:6],
        dependencies=["project-runner"],
        requires=["runtime"],
        risks=_review_note(run_commands),
        notes=["Use the run command from project-runner, then narrow the failing component."],
        certainty=Certainty.INFERENCE,
    )


PROJECT_DEBUGGER_RULE = PlanRule(
    id="project-debugger-rule",
    skill="project-debugger",
    title="Project debugger",
    priority=30,
    evaluate=evaluate_project_debugger,
    tags=("runtime", "debugging"),
)


# ------------------------------------------------------------ database debugger


#: Engines that normally run as a reachable server, worth a debugging skill.
_SERVER_ENGINES = frozenset(
    {
        "postgresql",
        "mysql",
        "mariadb",
        "mongodb",
        "mssql",
        "oracle",
        "clickhouse",
        "elasticsearch",
        "neo4j",
        "redis",
    }
)


def evaluate_database_debugger(context: PlanContext) -> SkillCandidate | None:
    databases = [
        database
        for database in context.databases
        if database.migration_tool
        or database.engine.value in _SERVER_ENGINES
        or database.config_files
    ]
    if not databases:
        return None
    engines = sorted({database.engine.label for database in databases})
    evidence = [item for database in databases for item in database.evidence][:6]
    reason = "Database dependencies detected: " + ", ".join(engines) + "."
    notes = []
    if context.services:
        notes.append(
            "Local services: " + ", ".join(sorted(service.name for service in context.services))
        )
    migration_tools = sorted(
        {database.migration_tool for database in databases if database.migration_tool}
    )
    if migration_tools:
        notes.append("Schema changes go through: " + ", ".join(migration_tools))
    return _candidate(
        DATABASE_DEBUGGER_RULE,
        reason=reason,
        evidence=evidence,
        requires=["database"],
        notes=notes,
        risks=["Inspecting a database is read-only; applying migrations is not."],
        certainty=Certainty.INFERENCE,
    )


DATABASE_DEBUGGER_RULE = PlanRule(
    id="database-debugger-rule",
    skill="database-debugger",
    title="Database debugger",
    priority=40,
    evaluate=evaluate_database_debugger,
    tags=("database",),
)


# ------------------------------------------------------------ migration guard


def evaluate_migration_guardian(context: PlanContext) -> SkillCandidate | None:
    databases = context.databases_with_migrations()
    if not databases:
        return None
    tools = sorted({database.migration_tool for database in databases if database.migration_tool})
    evidence = [item for database in databases for item in database.evidence][:6]
    commands = context.commands_of(CommandPurpose.MIGRATE)
    if commands:
        evidence.extend(context.evidence_for_commands(commands, limit=2))
    reason = f"Migration tooling detected: {', '.join(tools)}."
    return _candidate(
        MIGRATION_GUARDIAN_RULE,
        reason=reason,
        evidence=evidence,
        dependencies=["database-debugger"],
        requires=["database"],
        risks=[
            "Applying migrations changes database state and may be irreversible.",
            "Never run downgrades or resets against shared environments.",
        ],
        notes=["Always inspect pending revisions before applying them."],
        certainty=Certainty.FACT,
    )


MIGRATION_GUARDIAN_RULE = PlanRule(
    id="migration-guardian-rule",
    skill="migration-guardian",
    title="Migration guardian",
    priority=35,
    evaluate=evaluate_migration_guardian,
    tags=("database", "safety"),
)


# --------------------------------------------------------- api contract checker


def evaluate_api_contract_checker(context: PlanContext) -> SkillCandidate | None:
    apis = context.apis
    if not apis:
        return None
    frameworks = sorted({api.framework for api in apis if api.framework})
    evidence = [item for api in apis for item in api.evidence][:6]
    specs = sorted({path for api in apis for path in api.spec_paths})
    routers = sorted({path for api in apis for path in api.router_dirs})
    details: list[str] = []
    if specs:
        details.append("spec files: " + ", ".join(specs))
    if routers:
        details.append("route modules: " + ", ".join(routers))
    reason = "HTTP API surface detected"
    if frameworks:
        reason += f" ({', '.join(frameworks)})"
    reason += "."
    if details:
        reason += " " + "; ".join(details) + "."
    return _candidate(
        API_CONTRACT_CHECKER_RULE,
        reason=reason,
        evidence=evidence,
        dependencies=["project-runner"],
        requires=["runtime"],
        notes=["Check that documented routes match the implementation before changing contracts."],
        certainty=Certainty.INFERENCE,
    )


API_CONTRACT_CHECKER_RULE = PlanRule(
    id="api-contract-checker-rule",
    skill="api-contract-checker",
    title="API contract checker",
    priority=45,
    evaluate=evaluate_api_contract_checker,
    tags=("api",),
)


# ----------------------------------------------------------------- code review


def evaluate_code_reviewer(context: PlanContext) -> SkillCandidate | None:
    lint_commands = context.workflow_commands(WorkflowCategory.LINT)
    lint_commands = [
        command for command in lint_commands if command.command not in {"make lint"}
    ] or lint_commands
    if not lint_commands:
        return None
    tools = sorted({command.command for command in lint_commands})
    reason = "Static analysis tooling detected: " + ", ".join(tools[:4]) + "."
    commands_by_purpose = {command.purpose: command for command in lint_commands}
    verification = [
        command.command
        for purpose, command in commands_by_purpose.items()
        if purpose in (CommandPurpose.LINT, CommandPurpose.TYPECHECK, CommandPurpose.FORMAT)
    ]
    return _candidate(
        CODE_REVIEWER_RULE,
        reason=reason,
        evidence=context.evidence_for_commands(lint_commands),
        requires=[context.primary_language() or "runtime"],
        notes=[
            "Run the checks before reviewing a change: " + ", ".join(sorted(verification)),
            "Report findings with file:line references and do not rewrite code silently.",
        ],
        certainty=Certainty.FACT,
    )


CODE_REVIEWER_RULE = PlanRule(
    id="code-reviewer-rule",
    skill="code-reviewer",
    title="Code reviewer",
    priority=50,
    evaluate=evaluate_code_reviewer,
    tags=("quality",),
)


# --------------------------------------------------------------- release verify


def evaluate_release_verifier(context: PlanContext) -> SkillCandidate | None:
    deploy_commands = context.commands_of(CommandPurpose.DEPLOY, CommandPurpose.RELEASE)
    changelogs = context.docs("changelog")
    version_evidence: list[Evidence] = []
    for dependency in context.profile.dependencies:
        if dependency.name.lower() in {"setuptools-scm", "versioneer"}:
            version_evidence.extend(dependency.evidence)
    if deploy_commands:
        reason = (
            "Release or deployment steps detected: "
            + ", ".join(f"`{command.command}`" for command in deploy_commands[:3])
            + "."
        )
        evidence = context.evidence_for_commands(deploy_commands)
    elif changelogs:
        reason = f"Release documentation detected: {changelogs[0].path}."
        evidence = [
            Evidence(
                kind=Certainty.FACT,
                source=changelogs[0].path,
                locator="changelog",
                detail="changelog present",
                weight=0.6,
            )
        ]
    else:
        return None
    evidence = [*evidence, *version_evidence][:6]
    return _candidate(
        RELEASE_VERIFIER_RULE,
        reason=reason,
        evidence=evidence,
        dependencies=["test-runner"],
        risks=["Publishing or deploying changes state outside the repository."],
        notes=["Verify the test workflow and changelog before proposing a release."],
        certainty=Certainty.INFERENCE,
    )


RELEASE_VERIFIER_RULE = PlanRule(
    id="release-verifier-rule",
    skill="release-verifier",
    title="Release verifier",
    priority=55,
    evaluate=evaluate_release_verifier,
    tags=("release",),
)


# --------------------------------------------------------------------- registry

#: Rule order is the planning order; it is part of the deterministic contract.
DEFAULT_RULES: tuple[PlanRule, ...] = (
    PROJECT_RUNNER_RULE,
    TEST_RUNNER_RULE,
    PROJECT_BUILDER_RULE,
    PROJECT_DEBUGGER_RULE,
    MIGRATION_GUARDIAN_RULE,
    DATABASE_DEBUGGER_RULE,
    API_CONTRACT_CHECKER_RULE,
    CODE_REVIEWER_RULE,
    RELEASE_VERIFIER_RULE,
)

SUPPORTED_SKILLS: tuple[str, ...] = tuple(rule.skill for rule in DEFAULT_RULES)
