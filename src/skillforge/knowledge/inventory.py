"""Deep dependency inventory (K1).

Turns the analyzer's declared dependencies, detected technologies, and used
tooling into one classified inventory: relationship, scope, role, importance,
and — where the manifest already pins an exact version — a knowledge target.
Lockfile and installed-artifact resolution arrive in K2; this module never
guesses a version.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from skillforge.config import Settings
from skillforge.knowledge.versions import detect_pin
from skillforge.models import (
    Certainty,
    Dependency,
    DetectedTechnology,
    RepositoryProfile,
)
from skillforge.models.knowledge import (
    DependencyInventory,
    DependencyRelationship,
    DependencyRole,
    InventoryEntry,
    KnowledgePriority,
    KnowledgeTarget,
    VersionInfo,
)
from skillforge.models.repository import DependencyScope, TechnologyKind

#: Package registries whose resolved versions can become knowledge targets.
PACKAGE_ECOSYSTEMS = frozenset({"pypi", "npm", "nuget", "go", "pub", "maven"})

#: Technology kinds that are worth knowing about (CI/config/language are not).
_TECHNOLOGY_ROLES: dict[TechnologyKind, DependencyRole] = {
    TechnologyKind.RUNTIME: DependencyRole.RUNTIME,
    TechnologyKind.FRAMEWORK: DependencyRole.FRAMEWORK,
    TechnologyKind.LIBRARY: DependencyRole.LIBRARY,
    TechnologyKind.PACKAGE_MANAGER: DependencyRole.CLI_TOOL,
    TechnologyKind.BUILD_TOOL: DependencyRole.BUILD_TOOL,
    TechnologyKind.TEST_FRAMEWORK: DependencyRole.TEST_TOOL,
    TechnologyKind.DATABASE: DependencyRole.DATABASE,
    TechnologyKind.DATABASE_TOOL: DependencyRole.MIGRATION_TOOL,
    TechnologyKind.CONTAINER: DependencyRole.CONTAINER_TOOL,
    TechnologyKind.ORCHESTRATION: DependencyRole.INFRA_TOOL,
    TechnologyKind.CLOUD: DependencyRole.INFRA_TOOL,
    TechnologyKind.CACHE: DependencyRole.LIBRARY,
    TechnologyKind.QUEUE: DependencyRole.LIBRARY,
    TechnologyKind.WEB_SERVER: DependencyRole.LIBRARY,
    TechnologyKind.TOOL: DependencyRole.CLI_TOOL,
}

#: Ordered keyword table for names without a matching detected technology.
_ROLE_KEYWORDS: tuple[tuple[DependencyRole, tuple[str, ...]], ...] = (
    (
        DependencyRole.UI_COMPONENT_LIBRARY,
        (
            "@mui",
            "@material-ui",
            "antd",
            "@radix-ui",
            "@chakra-ui",
            "@mantine",
            "@headlessui",
            "bootstrap",
            "tailwind",
            "vuetify",
            "telerik",
            "kendo",
            "shadcn",
        ),
    ),
    (
        DependencyRole.FRAMEWORK,
        (
            "fastapi",
            "django",
            "flask",
            "starlette",
            "next",
            "react",
            "react-dom",
            "vue",
            "svelte",
            "angular",
            "@angular",
            "solid-js",
            "express",
            "fastify",
            "nest",
            "koa",
            "nuxt",
            "astro",
            "remix",
            "spring",
            "quarkus",
            "rails",
            "laravel",
            "gin",
            "echo",
            "fiber",
            "actix",
            "axum",
        ),
    ),
    (
        DependencyRole.ORM,
        (
            "sqlalchemy",
            "prisma",
            "typeorm",
            "sequelize",
            "mongoose",
            "gorm",
            "ent",
            "diesel",
            "hibernate",
            "entityframework",
            "efcore",
            "peewee",
            "tortoise",
        ),
    ),
    (
        DependencyRole.MIGRATION_TOOL,
        ("alembic", "flyway", "liquibase", "goose", "dbmate", "atlas", "knex", "dbup", "migra"),
    ),
    (
        DependencyRole.DATABASE,
        (
            "psycopg",
            "asyncpg",
            "pymysql",
            "mysqlclient",
            "pymongo",
            "redis",
            "mongodb",
            "npgsql",
            "sqlclient",
            "sqlite",
            "pg8000",
            "aiomysql",
            "clickhouse",
            "cassandra",
        ),
    ),
    (
        DependencyRole.SDK,
        (
            "boto3",
            "botocore",
            "stripe",
            "openai",
            "anthropic",
            "azure",
            "google-cloud",
            "aws-sdk",
            "twilio",
            "sendgrid",
            "supabase",
            "firebase",
            "@octokit",
            "slack",
        ),
    ),
    (
        DependencyRole.TEST_TOOL,
        (
            "pytest",
            "jest",
            "vitest",
            "mocha",
            "jasmine",
            "playwright",
            "cypress",
            "tox",
            "nox",
            "unittest",
            "gotestsum",
            "xunit",
            "nunit",
            "mstest",
            "rspec",
            "phpunit",
        ),
    ),
    (
        DependencyRole.LINTER,
        (
            "ruff",
            "eslint",
            "flake8",
            "pylint",
            "golangci",
            "clippy",
            "rubocop",
            "stylelint",
            "hadolint",
            "shellcheck",
            "tslint",
        ),
    ),
    (DependencyRole.FORMATTER, ("black", "prettier", "gofmt", "isort", "yapf", "dart_style")),
    (
        DependencyRole.BUILD_TOOL,
        (
            "vite",
            "webpack",
            "esbuild",
            "rollup",
            "parcel",
            "turbopack",
            "hatchling",
            "setuptools",
            "poetry-core",
            "pdm-backend",
            "flit-core",
            "gradle",
            "maven",
            "cmake",
            "meson",
            "ninja",
        ),
    ),
    (DependencyRole.CONTAINER_TOOL, ("docker", "podman", "containerd")),
    (
        DependencyRole.INFRA_TOOL,
        ("terraform", "pulumi", "ansible", "helm", "kubernetes", "serverless", "aws-cdk"),
    ),
    (
        DependencyRole.OBSERVABILITY,
        ("sentry", "opentelemetry", "datadog", "prometheus", "newrelic", "structlog", "loguru"),
    ),
    (
        DependencyRole.CODE_GENERATOR,
        ("protobuf", "grpc", "openapi", "graphql-codegen", "datamodel-code", "json-schema"),
    ),
)

#: First command token -> (display name, role) for tooling used but not declared.
_COMMAND_TOOLS: dict[str, tuple[str, DependencyRole]] = {
    "docker": ("Docker", DependencyRole.CONTAINER_TOOL),
    "docker-compose": ("Docker Compose", DependencyRole.CONTAINER_TOOL),
    "make": ("Make", DependencyRole.BUILD_TOOL),
    "task": ("Task", DependencyRole.BUILD_TOOL),
    "just": ("Just", DependencyRole.BUILD_TOOL),
    "pytest": ("pytest", DependencyRole.TEST_TOOL),
    "tox": ("tox", DependencyRole.TEST_TOOL),
    "ruff": ("ruff", DependencyRole.LINTER),
    "mypy": ("mypy", DependencyRole.LINTER),
    "eslint": ("ESLint", DependencyRole.LINTER),
    "prettier": ("Prettier", DependencyRole.FORMATTER),
    "tsc": ("TypeScript", DependencyRole.BUILD_TOOL),
    "vite": ("Vite", DependencyRole.BUILD_TOOL),
    "webpack": ("webpack", DependencyRole.BUILD_TOOL),
    "npm": ("npm", DependencyRole.CLI_TOOL),
    "pnpm": ("pnpm", DependencyRole.CLI_TOOL),
    "yarn": ("Yarn", DependencyRole.CLI_TOOL),
    "bun": ("Bun", DependencyRole.CLI_TOOL),
    "composer": ("Composer", DependencyRole.CLI_TOOL),
    "bundle": ("Bundler", DependencyRole.CLI_TOOL),
    "gradle": ("Gradle", DependencyRole.BUILD_TOOL),
    "gradlew": ("Gradle", DependencyRole.BUILD_TOOL),
    "./gradlew": ("Gradle", DependencyRole.BUILD_TOOL),
    "mvn": ("Maven", DependencyRole.BUILD_TOOL),
    "mvnw": ("Maven", DependencyRole.BUILD_TOOL),
    "./mvnw": ("Maven", DependencyRole.BUILD_TOOL),
    "alembic": ("Alembic", DependencyRole.MIGRATION_TOOL),
    "prisma": ("Prisma", DependencyRole.MIGRATION_TOOL),
    "flyway": ("Flyway", DependencyRole.MIGRATION_TOOL),
    "goose": ("goose", DependencyRole.MIGRATION_TOOL),
    "terraform": ("Terraform", DependencyRole.INFRA_TOOL),
    "kubectl": ("kubectl", DependencyRole.INFRA_TOOL),
    "helm": ("Helm", DependencyRole.INFRA_TOOL),
    "go": ("Go", DependencyRole.RUNTIME),
    "dotnet": (".NET", DependencyRole.RUNTIME),
    "flutter": ("Flutter", DependencyRole.RUNTIME),
    "dart": ("Dart", DependencyRole.RUNTIME),
    "cargo": ("Cargo", DependencyRole.BUILD_TOOL),
}

_SCOPE_RANK: dict[DependencyScope, int] = {
    DependencyScope.RUNTIME: 0,
    DependencyScope.DEVELOPMENT: 1,
    DependencyScope.PEER: 2,
    DependencyScope.OPTIONAL: 3,
    DependencyScope.UNKNOWN: 4,
}


#: Exact package names whose role differs from their keyword family.
_ROLE_NAME_OVERRIDES: dict[str, DependencyRole] = {
    "prisma": DependencyRole.MIGRATION_TOOL,
    "@prisma/client": DependencyRole.ORM,
    "alembic": DependencyRole.MIGRATION_TOOL,
    "typescript": DependencyRole.BUILD_TOOL,
    "ts-node": DependencyRole.BUILD_TOOL,
}


#: Packages that belong to a framework family but are not the framework itself.
_FRAMEWORK_COMPANIONS: frozenset[str] = frozenset(
    {"react-dom", "react-native", "react-server-dom-webpack"}
)


def _matches_keyword(lowered: str, keyword: str) -> bool:
    """Word-boundary keyword match; ``ent`` must not match ``sentry``."""
    return re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", lowered) is not None


def classify_role(name: str, technologies: list[DetectedTechnology]) -> DependencyRole:
    """Exact overrides, then keyword families, then detected-technology kind."""
    lowered = name.lower()
    override = _ROLE_NAME_OVERRIDES.get(lowered)
    if override is not None:
        return override
    base = lowered.split("/", 1)[-1]
    for role, keywords in _ROLE_KEYWORDS:
        for keyword in keywords:
            if not _matches_keyword(lowered, keyword):
                continue
            if role is DependencyRole.FRAMEWORK:
                # ``react-dom`` is part of React; ``@tanstack/react-query`` is
                # its own library that merely mentions React.
                if keyword.startswith("@"):
                    if lowered == keyword or lowered.startswith(keyword + "/"):
                        return role
                elif base == keyword or base in _FRAMEWORK_COMPANIONS:
                    return role
                continue
            return role
    for technology in technologies:
        tech_name = technology.name.lower()
        if not tech_name or len(tech_name) < 3:
            continue
        if tech_name == lowered or tech_name in lowered or lowered in tech_name:
            tech_role = _TECHNOLOGY_ROLES.get(technology.kind)
            if tech_role is not None:
                return tech_role
    return DependencyRole.LIBRARY


def _importance(
    *,
    relationship: DependencyRelationship,
    role: DependencyRole,
    scope: DependencyScope,
    overrides: dict[str, KnowledgePriority],
    name: str,
) -> KnowledgePriority:
    override = overrides.get(name.lower())
    if override is not None:
        return override
    if relationship is DependencyRelationship.TRANSITIVE:
        return KnowledgePriority.TRANSITIVE
    if scope in (DependencyScope.OPTIONAL, DependencyScope.PEER):
        return KnowledgePriority.LOW
    if scope is DependencyScope.DEVELOPMENT:
        if role in (
            DependencyRole.TEST_TOOL,
            DependencyRole.LINTER,
            DependencyRole.FORMATTER,
            DependencyRole.BUILD_TOOL,
        ):
            return KnowledgePriority.MEDIUM
        return KnowledgePriority.LOW
    if relationship is DependencyRelationship.DIRECT:
        if role in (DependencyRole.FRAMEWORK, DependencyRole.RUNTIME):
            return KnowledgePriority.CRITICAL
        if role in (
            DependencyRole.DATABASE,
            DependencyRole.ORM,
            DependencyRole.SDK,
            DependencyRole.UI_COMPONENT_LIBRARY,
            DependencyRole.MIGRATION_TOOL,
        ):
            return KnowledgePriority.HIGH
        return KnowledgePriority.MEDIUM
    if relationship is DependencyRelationship.WORKSPACE:
        return KnowledgePriority.MEDIUM
    return KnowledgePriority.LOW


def _priority_overrides(settings: Settings) -> dict[str, KnowledgePriority]:
    overrides: dict[str, KnowledgePriority] = {}
    for name, value in settings.knowledge.priority.items():
        parsed = KnowledgePriority.from_config(value)
        if parsed is not None:
            overrides[name.lower()] = parsed
    return overrides


def _entry_from_dependency(
    dependency: Dependency,
    *,
    technologies: list[DetectedTechnology],
    overrides: dict[str, KnowledgePriority],
) -> InventoryEntry:
    role = classify_role(dependency.name, technologies)
    resolved, method = detect_pin(dependency.version_spec, dependency.ecosystem)
    certainty = Certainty.FACT if resolved else Certainty.INFERENCE
    return InventoryEntry(
        id=InventoryEntry.make_id(dependency.ecosystem, dependency.name),
        ecosystem=dependency.ecosystem,
        name=dependency.name,
        relationship=DependencyRelationship.DIRECT,
        role=role,
        scope=dependency.scope,
        importance=_importance(
            relationship=DependencyRelationship.DIRECT,
            role=role,
            scope=dependency.scope,
            overrides=overrides,
            name=dependency.name,
        ),
        version=VersionInfo(
            declared=dependency.version_spec,
            resolved=resolved,
            method=method,
            resolved_from=dependency.manifest or None,
            certainty=certainty,
        ),
        manifests=[dependency.manifest] if dependency.manifest else [],
        evidence=list(dependency.evidence),
    )


def _entry_from_technology(
    technology: DetectedTechnology, *, overrides: dict[str, KnowledgePriority]
) -> InventoryEntry | None:
    role = _TECHNOLOGY_ROLES.get(technology.kind)
    if role is None or technology.kind is TechnologyKind.LANGUAGE:
        return None
    ecosystem = "runtime" if role is DependencyRole.RUNTIME else "tool"
    return InventoryEntry(
        id=InventoryEntry.make_id(ecosystem, technology.name),
        ecosystem=ecosystem,
        name=technology.name,
        relationship=DependencyRelationship.DIRECT,
        role=role,
        scope=DependencyScope.UNKNOWN,
        importance=_importance(
            relationship=DependencyRelationship.DIRECT,
            role=role,
            scope=DependencyScope.UNKNOWN,
            overrides=overrides,
            name=technology.name,
        ),
        version=VersionInfo(
            declared=technology.version,
            notes=[technology.display] if technology.version else [],
        ),
        evidence=list(technology.evidence),
        notes=["detected technology"],
    )


def _command_tool_entries(
    profile: RepositoryProfile,
    *,
    covered: set[str],
    overrides: dict[str, KnowledgePriority],
) -> list[InventoryEntry]:
    entries: list[InventoryEntry] = []
    for command in profile.commands:
        tokens = command.command.split()
        if not tokens:
            continue
        tool = _COMMAND_TOOLS.get(tokens[0].lower())
        if tool is None:
            continue
        display, role = tool
        if display.lower() in covered:
            continue
        covered.add(display.lower())
        entries.append(
            InventoryEntry(
                id=InventoryEntry.make_id("tool", display),
                ecosystem="tool",
                name=display,
                relationship=DependencyRelationship.DIRECT,
                role=role,
                scope=DependencyScope.UNKNOWN,
                importance=_importance(
                    relationship=DependencyRelationship.DIRECT,
                    role=role,
                    scope=DependencyScope.UNKNOWN,
                    overrides=overrides,
                    name=display,
                ),
                version=VersionInfo(),
                evidence=list(command.evidence),
                notes=["used by repository commands"],
            )
        )
    return entries


def _merge_entry(existing: InventoryEntry, new: InventoryEntry) -> InventoryEntry:
    merged = existing.model_copy(deep=True)
    merged.manifests = sorted(set(existing.manifests) | set(new.manifests))
    merged.lockfiles = sorted(set(existing.lockfiles) | set(new.lockfiles))
    merged.evidence = [*existing.evidence, *new.evidence]
    merged.notes = sorted(set(existing.notes) | set(new.notes))
    if _SCOPE_RANK[new.scope] < _SCOPE_RANK[merged.scope]:
        merged.scope = new.scope
    if new.importance.rank < merged.importance.rank:
        merged.importance = new.importance
    if not merged.version.declared and new.version.declared:
        merged.version = new.version
    return merged


def _sort_key(entry: InventoryEntry) -> tuple[int, str, str, str]:
    return (entry.importance.rank, entry.ecosystem, entry.name.lower(), entry.component or "")


def build_inventory(profile: RepositoryProfile, settings: Settings) -> DependencyInventory:
    """Build the classified inventory for one analyzed repository."""
    overrides = _priority_overrides(settings)
    entries: dict[str, InventoryEntry] = {}
    for dependency in profile.dependencies:
        candidate = _entry_from_dependency(
            dependency, technologies=profile.technologies, overrides=overrides
        )
        existing = entries.get(candidate.key)
        entries[candidate.key] = (
            candidate if existing is None else _merge_entry(existing, candidate)
        )

    covered = {entry.name.lower() for entry in entries.values()}
    for technology in profile.technologies:
        if technology.name.lower() in covered:
            continue
        tech_entry = _entry_from_technology(technology, overrides=overrides)
        if tech_entry is None:
            continue
        covered.add(tech_entry.name.lower())
        existing = entries.get(tech_entry.key)
        entries[tech_entry.key] = (
            tech_entry if existing is None else _merge_entry(existing, tech_entry)
        )

    for candidate in _command_tool_entries(profile, covered=covered, overrides=overrides):
        entries.setdefault(candidate.key, candidate)

    ordered = sorted(entries.values(), key=_sort_key)
    warnings: list[str] = []
    truncated = False
    limit = settings.knowledge.max_inventory_entries
    if len(ordered) > limit:
        truncated = True
        ordered = ordered[:limit]
        warnings.append(
            f"dependency inventory truncated at {limit} entries "
            f"(most important kept); increase knowledge.max_inventory_entries to keep more"
        )

    targets: dict[str, KnowledgeTarget] = {}
    for entry in ordered:
        if entry.ecosystem not in PACKAGE_ECOSYSTEMS or not entry.version.is_resolved:
            continue
        target = KnowledgeTarget(
            ecosystem=entry.ecosystem,
            technology=entry.name,
            version=entry.version.resolved or "",
        )
        targets.setdefault(target.key, target)

    return DependencyInventory(
        repository=profile.name,
        generated_at=datetime.now(UTC),
        entries=ordered,
        knowledge_targets=sorted(
            targets.values(), key=lambda item: (item.ecosystem, item.technology.lower())
        ),
        truncated=truncated,
        warnings=warnings,
    )


__all__ = [
    "PACKAGE_ECOSYSTEMS",
    "build_inventory",
    "classify_role",
    "detect_pin",
]
