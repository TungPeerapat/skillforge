"""Repository profile assembly.

Runs the scanner and detectors, merges their findings deterministically, and
produces a :class:`~skillforge.models.repository.RepositoryProfile`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from skillforge import __version__
from skillforge.analyzer.detectors.base import Detection, DetectionContext
from skillforge.analyzer.detectors.registry import default_detectors, run_detectors
from skillforge.analyzer.scanner import ScanOptions, ScanResult, scan_repository
from skillforge.config import Settings
from skillforge.discovery.commands import dedupe_evidence
from skillforge.discovery.workflows import synthesize_workflows
from skillforge.logging import get_logger, trace_span
from skillforge.models import (
    API,
    Certainty,
    Command,
    ComponentKind,
    Database,
    Dependency,
    DetectedTechnology,
    DocFile,
    FileStats,
    GitSummary,
    LanguageStat,
    ProjectComponent,
    RepositoryProfile,
    Risk,
    Service,
    Severity,
    Unknown,
)
from skillforge.models.skill import SkillPlan
from skillforge.models.workflow import DatabaseEngine, RiskCategory, ServiceKind

logger = get_logger("analyzer.profile")

_PURPOSE_ORDER = {
    "setup": 0,
    "run": 1,
    "test": 2,
    "build": 3,
    "lint": 4,
    "format": 5,
    "typecheck": 6,
    "migrate": 7,
    "seed": 8,
    "verify": 9,
    "docs": 10,
    "deploy": 11,
    "release": 12,
    "clean": 13,
    "other": 14,
}

_SOURCE_ORDER = {
    "package_script": 0,
    "makefile": 1,
    "taskfile": 2,
    "procfile": 3,
    "compose": 4,
    "dockerfile": 5,
    "config": 6,
    "ci": 7,
    "readme": 8,
    "docs": 9,
    "justfile": 10,
    "other": 11,
}

_KIND_ORDER = {
    ComponentKind.APPLICATION: 5,
    ComponentKind.CLI: 4,
    ComponentKind.SERVICE: 3,
    ComponentKind.PACKAGE: 2,
    ComponentKind.LIBRARY: 1,
    ComponentKind.UNKNOWN: 0,
}

_GENERIC_COMPONENT_NAMES = frozenset(
    {
        "app",
        "python",
        "node",
        "go",
        "java",
        "dotnet",
        "flutter",
        "dart",
        "package",
        "service",
        "application",
        "root",
    }
)

_ENGINE_BY_IMAGE: tuple[tuple[str, str], ...] = (
    ("postgres", "postgresql"),
    ("mysql", "mysql"),
    ("mariadb", "mariadb"),
    ("mongo", "mongodb"),
    ("redis", "redis"),
    ("mssql", "mssql"),
    ("clickhouse", "clickhouse"),
    ("elasticsearch", "elasticsearch"),
    ("neo4j", "neo4j"),
)

_IMAGE_ENGINE_BY_NAME: dict[str, str] = {
    "postgresql": "postgresql",
    "postgres": "postgresql",
    "mysql": "mysql",
    "mariadb": "mariadb",
    "mongodb": "mongodb",
    "mongo": "mongodb",
    "redis": "redis",
    "valkey": "redis",
    "mssql": "mssql",
    "sqlserver": "mssql",
    "sqlite": "sqlite",
    "clickhouse": "clickhouse",
}


@dataclass
class AnalysisResult:
    """Profile plus the scan it was built from (the scan feeds context selection)."""

    profile: RepositoryProfile
    scan: ScanResult


def scan_options_from_settings(settings: Settings) -> ScanOptions:
    """Map validated configuration onto scanner options."""
    return ScanOptions(
        max_file_size=settings.analysis.max_file_size,
        follow_symlinks=settings.analysis.follow_symlinks,
        include=tuple(settings.analysis.include),
        extra_ignore=tuple(settings.analysis.ignore),
    )


def analyze_repository(
    root: Path,
    settings: Settings,
    *,
    scan: ScanResult | None = None,
    scan_options: ScanOptions | None = None,
) -> AnalysisResult:
    """Analyse ``root`` and return the profile together with the scan."""
    resolved = root.resolve()
    with trace_span("analyze", root=str(resolved)):
        actual_scan = scan or scan_repository(
            resolved, scan_options or scan_options_from_settings(settings)
        )
        profile = build_profile(resolved, actual_scan, settings)
    return AnalysisResult(profile=profile, scan=actual_scan)


def build_profile(root: Path, scan: ScanResult, settings: Settings) -> RepositoryProfile:
    """Run detectors and merge everything into a :class:`RepositoryProfile`."""
    context = DetectionContext(root, scan, settings.analysis)
    detector_results = run_detectors(context, default_detectors())
    merged = Detection()
    detector_notes: list[str] = []
    for detector_id, detection in detector_results:
        if not isinstance(detection, Detection):  # pragma: no cover - defensive
            continue
        merged.merge(detection)
        for note in detection.notes:
            detector_notes.append(f"{detector_id}: {note}")

    components = _merge_components(merged.components, root.name)
    technologies = _merge_technologies(merged.technologies)
    dependencies = _merge_dependencies(merged.dependencies)
    commands = _merge_commands(merged.commands)
    services = _merge_services(merged.services)
    databases = _merge_databases(merged.databases, services)
    apis = _merge_apis(merged.apis)
    docs = _merge_docs(merged.docs)
    risks = _merge_risks(merged.risks)
    unknowns = _merge_unknowns(merged.unknowns)
    if any(service.kind is ServiceKind.DATABASE for service in services):
        unknowns = [item for item in unknowns if item.id != "dev-database"]
    unknowns.extend(_service_unknowns(services))

    dangerous = [command for command in commands if command.risk.is_dangerous]
    if dangerous:
        risks.append(
            Risk(
                id="destructive-commands",
                title="Destructive command patterns found in the repository",
                description=(
                    f"{len(dangerous)} discovered command(s) match destructive patterns "
                    "(for example recursive deletes or piping remote scripts into a shell). "
                    "They are never executed by SkillForge and are excluded from generated scripts."
                ),
                severity=Severity.HIGH,
                category=RiskCategory.SECURITY,
                evidence=[item for command in dangerous[:5] for item in command.evidence],
                mitigation="Review these commands manually; do not let an agent run them unattended.",
            )
        )

    risky = [command for command in commands if not command.risk.is_safe]
    if risky:
        risks.append(
            Risk(
                id="commands-need-review",
                title="Some discovered commands modify state",
                description=(
                    f"{len(risky)} command(s) can change the machine, repository, or a database "
                    "(installs, containers, migrations, git operations). Confirm before running them."
                ),
                severity=Severity.MEDIUM,
                category=RiskCategory.RELIABILITY,
                evidence=[item for command in risky[:5] for item in command.evidence],
                mitigation="Run read-only checks first; use generated scripts which refuse to run review-level steps without confirmation.",
            )
        )

    workflows = synthesize_workflows(
        commands,
        components=components,
        services=services,
        databases=databases,
        apis=apis,
        repo_name=root.name,
    )
    stats = _file_stats(scan)
    git = GitSummary(
        available=scan.git.available,
        in_work_tree=scan.git.in_work_tree,
        commit=scan.git.commit,
        branch=scan.git.branch,
        dirty=scan.git.dirty,
        host=scan.git.host,
    )
    warnings = list(dict.fromkeys([*scan.warnings, *detector_notes]))
    profile = RepositoryProfile(
        tool_version=__version__,
        generated_at=datetime.now(UTC),
        name=settings.project_name(root),
        root_path=str(root),
        git=git,
        stats=stats,
        components=components,
        technologies=technologies,
        dependencies=dependencies,
        commands=commands,
        workflows=workflows,
        services=services,
        databases=databases,
        apis=apis,
        docs=docs,
        risks=_sort_risks(risks),
        unknowns=unknowns,
        environment_keys=sorted(set(merged.env_keys)),
        environment_files=sorted(set(merged.env_files)),
        warnings=warnings,
    )
    logger.debug(
        "profile built",
        extra={
            "technologies": len(technologies),
            "commands": len(commands),
            "workflows": len(workflows),
            "components": len(components),
        },
    )
    return profile


# --------------------------------------------------------------------- mergers


def _merge_components(components: list[ProjectComponent], repo_name: str) -> list[ProjectComponent]:
    by_path: dict[str, ProjectComponent] = {}
    for component in components:
        existing = by_path.get(component.path)
        if existing is None:
            by_path[component.path] = component.model_copy(deep=True)
            continue
        merged = existing.model_copy(deep=True)
        merged.ecosystems = sorted(set(existing.ecosystems) | set(component.ecosystems))
        merged.technologies = sorted(set(existing.technologies) | set(component.technologies))
        merged.manifests = sorted(set(existing.manifests) | set(component.manifests))
        merged.evidence = dedupe_evidence([*existing.evidence, *component.evidence])
        merged.confidence = round(min(0.99, max(existing.confidence, component.confidence)), 3)
        if _KIND_ORDER[component.kind] > _KIND_ORDER[existing.kind]:
            merged.kind = component.kind
        if (
            merged.name in _GENERIC_COMPONENT_NAMES
            and component.name not in _GENERIC_COMPONENT_NAMES
        ):
            merged.name = component.name
        by_path[component.path] = merged

    result: list[ProjectComponent] = []
    for component in by_path.values():
        if component.path == "." and component.name in _GENERIC_COMPONENT_NAMES:
            component = component.model_copy(update={"name": _slug(repo_name)})
        result.append(component)
    result.sort(key=lambda item: (item.path != ".", item.path))
    return result


def _slug(value: str) -> str:
    from skillforge.utils.text import slugify

    return slugify(value, max_length=48) or "project"


def _merge_technologies(items: list[DetectedTechnology]) -> list[DetectedTechnology]:
    groups: dict[tuple[str, str], DetectedTechnology] = {}
    for item in items:
        key = (item.kind.value, item.name.lower())
        existing = groups.get(key)
        if existing is None:
            groups[key] = item.model_copy(deep=True)
            continue
        merged = existing.model_copy(deep=True)
        merged.version = existing.version or item.version
        merged.certainty = _stronger_certainty(existing.certainty, item.certainty)
        merged.confidence = round(min(0.99, max(existing.confidence, item.confidence)), 3)
        merged.evidence = dedupe_evidence([*existing.evidence, *item.evidence])
        merged.notes = sorted(set(existing.notes) | set(item.notes))
        groups[key] = merged
    result = sorted(groups.values(), key=lambda item: (item.kind.value, item.name.lower()))
    return result


def _stronger_certainty(first: Certainty, second: Certainty) -> Certainty:
    order = {Certainty.UNKNOWN: 0, Certainty.INFERENCE: 1, Certainty.FACT: 2}
    return first if order[first] >= order[second] else second


def _merge_dependencies(items: list[Dependency]) -> list[Dependency]:
    groups: dict[tuple[str, str, str], Dependency] = {}
    for item in items:
        key = (item.ecosystem, item.name.lower(), item.manifest)
        existing = groups.get(key)
        if existing is None:
            groups[key] = item.model_copy(deep=True)
            continue
        merged = existing.model_copy(deep=True)
        merged.version_spec = existing.version_spec or item.version_spec
        merged.evidence = dedupe_evidence([*existing.evidence, *item.evidence])
        groups[key] = merged
    return sorted(
        groups.values(), key=lambda item: (item.ecosystem, item.name.lower(), item.manifest)
    )


def _merge_commands(items: list[Command]) -> list[Command]:
    items = _canonicalize_script_commands(items)
    groups: dict[tuple[str, str], Command] = {}
    for item in items:
        key = (item.command, item.cwd)
        existing = groups.get(key)
        if existing is None:
            groups[key] = item.model_copy(deep=True)
            continue
        merged = existing.model_copy(deep=True)
        merged.evidence = dedupe_evidence([*existing.evidence, *item.evidence])
        if merged.purpose.value == "other" and item.purpose.value != "other":
            merged.purpose = item.purpose
        if _SOURCE_ORDER.get(item.source.value, 99) < _SOURCE_ORDER.get(existing.source.value, 99):
            merged.source = item.source
            merged.certainty = item.certainty
            merged.confidence = item.confidence
        merged.confidence = round(min(0.99, max(existing.confidence, item.confidence)), 3)
        merged.notes = sorted(set(existing.notes) | set(item.notes))
        merged.risk = max(existing.risk, item.risk)
        merged.risk_reasons = sorted(set(existing.risk_reasons) | set(item.risk_reasons))
        groups[key] = merged
    result = sorted(
        groups.values(),
        key=lambda item: (
            _PURPOSE_ORDER.get(item.purpose.value, 99),
            _SOURCE_ORDER.get(item.source.value, 99),
            item.command,
            item.cwd,
        ),
    )
    return result


def _canonicalize_script_commands(items: list[Command]) -> list[Command]:
    """Rewrite short-form package-manager invocations to their canonical script form.

    ``pnpm test`` in a CI file and ``pnpm run test`` from package.json are the same
    command; canonicalizing lets them merge into one evidence-backed entry.
    """
    script_map: dict[str, str] = {}
    for command in items:
        if command.source.value != "package_script":
            continue
        parts = command.command.split()
        if len(parts) >= 3 and parts[0] in {"npm", "pnpm", "yarn", "bun"} and parts[1] == "run":
            script_map.setdefault(parts[2], command.command)
    if not script_map:
        return items
    rewritten: list[Command] = []
    for command in items:
        if command.source.value == "package_script":
            rewritten.append(command)
            continue
        parts = command.command.split()
        if (
            len(parts) == 2
            and parts[0] in {"npm", "pnpm", "yarn", "bun"}
            and parts[1] in script_map
        ):
            rewritten.append(
                command.model_copy(
                    update={
                        "command": script_map[parts[1]],
                        "notes": [*command.notes, f"normalized from '{command.command}'"],
                    }
                )
            )
            continue
        rewritten.append(command)
    return rewritten


def _merge_services(items: list[Service]) -> list[Service]:
    groups: dict[str, Service] = {}
    for item in items:
        existing = groups.get(item.name)
        if existing is None:
            groups[item.name] = item.model_copy(deep=True)
            continue
        merged = existing.model_copy(deep=True)
        merged.image = existing.image or item.image
        merged.ports = sorted(set(existing.ports) | set(item.ports))
        merged.environment_keys = sorted(
            set(existing.environment_keys) | set(item.environment_keys)
        )
        merged.evidence = dedupe_evidence([*existing.evidence, *item.evidence])
        merged.confidence = round(min(0.99, max(existing.confidence, item.confidence)), 3)
        if existing.kind is ServiceKind.OTHER:
            merged.kind = item.kind
        groups[item.name] = merged
    return sorted(groups.values(), key=lambda item: item.name)


def _merge_databases(items: list[Database], services: list[Service]) -> list[Database]:

    for service in services:
        engine = _engine_from_service(service)
        if engine is None:
            continue
        items.append(
            Database(
                engine=engine,
                evidence=list(service.evidence),
                certainty=Certainty.INFERENCE,
                confidence=0.75,
            )
        )
    known: dict[str, Database] = {}
    unknown: list[Database] = []
    for item in items:
        if item.engine.value == "unknown":
            unknown.append(item.model_copy(deep=True))
            continue
        key = item.engine.value
        existing = known.get(key)
        if existing is None:
            known[key] = item.model_copy(deep=True)
            continue
        merged = existing.model_copy(deep=True)
        merged.orm = existing.orm or item.orm
        merged.driver = existing.driver or item.driver
        merged.migration_tool = existing.migration_tool or item.migration_tool
        merged.migration_dirs = sorted(set(existing.migration_dirs) | set(item.migration_dirs))
        merged.config_files = sorted(set(existing.config_files) | set(item.config_files))
        merged.evidence = dedupe_evidence([*existing.evidence, *item.evidence])
        merged.confidence = round(min(0.99, max(existing.confidence, item.confidence)), 3)
        merged.certainty = _stronger_certainty(existing.certainty, item.certainty)
        known[key] = merged

    kept_unknown: list[Database] = []
    for entry in unknown:
        target = _unknown_target(entry, known)
        if target is not None:
            target.migration_tool = target.migration_tool or entry.migration_tool
            target.migration_dirs = sorted(set(target.migration_dirs) | set(entry.migration_dirs))
            target.config_files = sorted(set(target.config_files) | set(entry.config_files))
            target.evidence = dedupe_evidence([*target.evidence, *entry.evidence])
            continue
        match = next(
            (item for item in kept_unknown if item.migration_tool == entry.migration_tool), None
        )
        if match:
            match.migration_dirs = sorted(set(match.migration_dirs) | set(entry.migration_dirs))
            match.evidence = dedupe_evidence([*match.evidence, *entry.evidence])
        else:
            kept_unknown.append(entry)
    result = [*known.values(), *kept_unknown]
    result.sort(key=lambda item: (item.engine.value, item.orm or "", item.migration_tool or ""))
    return result


_RELATIONAL_ENGINES = frozenset({"postgresql", "mysql", "mariadb", "sqlite", "mssql", "oracle"})


def _unknown_target(entry: Database, known: dict[str, Database]) -> Database | None:
    """Choose the known database a schema-only entry belongs to, if unambiguous."""
    if not known:
        return None
    if len(known) == 1:
        return next(iter(known.values()))
    relational = [item for key, item in known.items() if key in _RELATIONAL_ENGINES]
    if len(relational) == 1 and (entry.migration_tool or entry.orm):
        return relational[0]
    return None


def _engine_from_service(service: Service) -> DatabaseEngine | None:
    lowered = f"{service.name} {service.image or ''}".lower()
    engine_name = _IMAGE_ENGINE_BY_NAME.get(
        next((token for token in _IMAGE_ENGINE_BY_NAME if token in lowered), "")
    )
    if engine_name is None:
        return None
    return DatabaseEngine(engine_name)


def _merge_apis(items: list[API]) -> list[API]:
    groups: dict[tuple[str, str], API] = {}
    for item in items:
        key = (item.kind.value, (item.framework or "").lower())
        existing = groups.get(key)
        if existing is None:
            groups[key] = item.model_copy(deep=True)
            continue
        merged = existing.model_copy(deep=True)
        merged.router_dirs = sorted(set(existing.router_dirs) | set(item.router_dirs))
        merged.spec_paths = sorted(set(existing.spec_paths) | set(item.spec_paths))
        merged.entrypoints = sorted(set(existing.entrypoints) | set(item.entrypoints))
        merged.evidence = dedupe_evidence([*existing.evidence, *item.evidence])
        merged.confidence = round(min(0.99, max(existing.confidence, item.confidence)), 3)
        groups[key] = merged
    return sorted(groups.values(), key=lambda item: (item.kind.value, item.framework or ""))


def _merge_docs(items: list[DocFile]) -> list[DocFile]:
    by_path: dict[str, DocFile] = {}
    for item in items:
        by_path.setdefault(item.path, item)
    return sorted(by_path.values(), key=lambda item: item.path)


def _merge_risks(items: list[Risk]) -> list[Risk]:
    groups: dict[str, Risk] = {}
    for item in items:
        existing = groups.get(item.id)
        if existing is None:
            groups[item.id] = item.model_copy(deep=True)
            continue
        merged = existing.model_copy(deep=True)
        merged.evidence = dedupe_evidence([*existing.evidence, *item.evidence])
        severity_order = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
        if severity_order[item.severity.value] > severity_order[existing.severity.value]:
            merged.severity = item.severity
        groups[item.id] = merged
    return list(groups.values())


def _sort_risks(items: list[Risk]) -> list[Risk]:
    order = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
    return sorted(items, key=lambda item: (-order[item.severity.value], item.id))


def _merge_unknowns(items: list[Unknown]) -> list[Unknown]:
    groups: dict[str, Unknown] = {}
    for item in items:
        groups.setdefault(item.id, item)
    return sorted(groups.values(), key=lambda item: item.id)


def _service_unknowns(services: list[Service]) -> list[Unknown]:
    relevant = [
        service
        for service in services
        if service.kind in (ServiceKind.DATABASE, ServiceKind.CACHE, ServiceKind.QUEUE)
        # CI-only services are not necessarily required for local development.
        and service.is_local_dependency
    ]
    return [
        Unknown(
            id=f"service-{item.name}",
            question=f"Is '{item.name}' required for local development, and how is it reached?",
            why="A dependency service was found in configuration, but its usage in local development was not confirmed.",
            suggested_check=f"Inspect the service definition, then try it with `docker compose up {item.name}`.",
            evidence=list(item.evidence),
        )
        for item in relevant
    ]


def _file_stats(scan: ScanResult) -> FileStats:
    languages: dict[str, LanguageStat] = {}
    categories: dict[str, int] = {}
    total_lines = 0
    for record in scan.files:
        categories[record.category.value] = categories.get(record.category.value, 0) + 1
        content = scan.contents.get(record.path)
        lines = content.count("\n") + 1 if content else 0
        total_lines += lines
        bucket = languages.setdefault(record.language, LanguageStat(language=record.language))
        bucket.files += 1
        bucket.lines += lines
    language_list = sorted(languages.values(), key=lambda item: (-item.lines, item.language))
    return FileStats(
        total_files=len(scan.files),
        analyzed_files=len(scan.contents),
        skipped_files=len(scan.skipped),
        ignored_entries=scan.ignored_entries,
        total_bytes=scan.total_bytes,
        analyzed_bytes=scan.analyzed_bytes,
        total_lines=total_lines,
        truncated_files=len(scan.truncated_files),
        languages=language_list,
        categories=dict(sorted(categories.items())),
        skipped_reasons=scan.skipped_by_reason(),
    )


# --------------------------------------------------------------- skill planning


def plan_skills(profile: RepositoryProfile) -> SkillPlan:
    """Convenience wrapper so callers do not import the planner twice."""
    from skillforge.planner.planner import SkillPlanner

    return SkillPlanner().plan(profile)


__all__ = [
    "AnalysisResult",
    "analyze_repository",
    "build_profile",
    "plan_skills",
    "scan_options_from_settings",
]
