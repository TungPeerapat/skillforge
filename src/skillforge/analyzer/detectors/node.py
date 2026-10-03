"""Node.js / TypeScript ecosystem detector."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any, Final

from skillforge.analyzer.detectors.base import Detection, DetectionContext, fact
from skillforge.discovery.commands import build_command, classify_purpose, script_command
from skillforge.models import (
    API,
    Certainty,
    ComponentKind,
    Database,
    Dependency,
    DependencyScope,
    DetectedTechnology,
    Evidence,
    ProjectComponent,
    Risk,
    Severity,
    TechnologyKind,
)
from skillforge.models.workflow import APIKind, CommandPurpose, CommandSource, RiskCategory
from skillforge.security.command_risk import classify_command
from skillforge.utils.globs import match_path
from skillforge.utils.text import slugify

_FRAMEWORKS: Final[dict[str, tuple[str, TechnologyKind]]] = {
    "next": ("Next.js", TechnologyKind.FRAMEWORK),
    "nuxt": ("Nuxt", TechnologyKind.FRAMEWORK),
    "@remix-run/react": ("Remix", TechnologyKind.FRAMEWORK),
    "astro": ("Astro", TechnologyKind.FRAMEWORK),
    "@sveltejs/kit": ("SvelteKit", TechnologyKind.FRAMEWORK),
    "svelte": ("Svelte", TechnologyKind.FRAMEWORK),
    "react": ("React", TechnologyKind.FRAMEWORK),
    "vue": ("Vue", TechnologyKind.FRAMEWORK),
    "angular": ("Angular", TechnologyKind.FRAMEWORK),
    "@angular/core": ("Angular", TechnologyKind.FRAMEWORK),
    "express": ("Express", TechnologyKind.FRAMEWORK),
    "fastify": ("Fastify", TechnologyKind.FRAMEWORK),
    "@nestjs/core": ("NestJS", TechnologyKind.FRAMEWORK),
    "koa": ("Koa", TechnologyKind.FRAMEWORK),
    "hono": ("Hono", TechnologyKind.FRAMEWORK),
    "vite": ("Vite", TechnologyKind.BUILD_TOOL),
    "webpack": ("webpack", TechnologyKind.BUILD_TOOL),
    "esbuild": ("esbuild", TechnologyKind.BUILD_TOOL),
    "turbo": ("Turborepo", TechnologyKind.BUILD_TOOL),
    "typescript": ("TypeScript", TechnologyKind.LANGUAGE),
    "prisma": ("Prisma", TechnologyKind.DATABASE_TOOL),
    "@prisma/client": ("Prisma", TechnologyKind.DATABASE_TOOL),
    "drizzle-orm": ("Drizzle ORM", TechnologyKind.LIBRARY),
    "sequelize": ("Sequelize", TechnologyKind.LIBRARY),
    "typeorm": ("TypeORM", TechnologyKind.LIBRARY),
    "mongoose": ("Mongoose", TechnologyKind.LIBRARY),
    "jest": ("Jest", TechnologyKind.TEST_FRAMEWORK),
    "vitest": ("Vitest", TechnologyKind.TEST_FRAMEWORK),
    "playwright": ("Playwright", TechnologyKind.TEST_FRAMEWORK),
    "@playwright/test": ("Playwright", TechnologyKind.TEST_FRAMEWORK),
    "cypress": ("Cypress", TechnologyKind.TEST_FRAMEWORK),
    "eslint": ("ESLint", TechnologyKind.TOOL),
    "prettier": ("Prettier", TechnologyKind.TOOL),
    "tailwindcss": ("Tailwind CSS", TechnologyKind.LIBRARY),
    "pg": ("PostgreSQL", TechnologyKind.DATABASE),
    "postgres": ("PostgreSQL", TechnologyKind.DATABASE),
    "mongodb": ("MongoDB", TechnologyKind.DATABASE),
    "redis": ("Redis", TechnologyKind.CACHE),
    "ioredis": ("Redis", TechnologyKind.CACHE),
    "mysql2": ("MySQL", TechnologyKind.DATABASE),
    "better-sqlite3": ("SQLite", TechnologyKind.DATABASE),
    "serverless": ("Serverless Framework", TechnologyKind.TOOL),
}

_MANAGER_BY_LOCKFILE: Final[tuple[tuple[str, str], ...]] = (
    ("pnpm-lock.yaml", "pnpm"),
    ("yarn.lock", "yarn"),
    ("bun.lockb", "bun"),
    ("package-lock.json", "npm"),
    ("npm-shrinkwrap.json", "npm"),
)

_DATABASE_ENGINES: Final[dict[str, str]] = {
    "pg": "postgresql",
    "postgres": "postgresql",
    "postgresql": "postgresql",
    "mysql2": "mysql",
    "mysql": "mysql",
    "better-sqlite3": "sqlite",
    "sqlite3": "sqlite",
    "mongodb": "mongodb",
    "mongoose": "mongodb",
    "redis": "redis",
    "ioredis": "redis",
    "mssql": "mssql",
}

_DEPENDENCY_LIMIT = 200

#: npm lifecycle hooks run automatically on install; they are reported as risks
#: and notes rather than as user-facing workflow commands.
_LIFECYCLE_HOOKS: Final[frozenset[str]] = frozenset(
    {"preinstall", "install", "postinstall", "prepare", "prepublish", "prepublishonly", "prepack"}
)


class NodeDetector:
    """Detects Node.js/TypeScript projects, scripts, and monorepo workspaces."""

    id = "node"

    def applies(self, context: DetectionContext) -> bool:
        return bool(context.records_named("package.json"))

    def detect(self, context: DetectionContext) -> Detection:
        detection = Detection()
        for record in context.records_named("package.json"):
            self._detect_package(context, record.path, detection)
        if context.exists("tsconfig.json") and not any(
            item.name == "TypeScript" for item in detection.technologies
        ):
            detection.technologies.append(
                DetectedTechnology(
                    kind=TechnologyKind.LANGUAGE,
                    name="TypeScript",
                    certainty=Certainty.FACT,
                    confidence=0.8,
                    evidence=[fact("tsconfig.json", weight=0.8)],
                )
            )
        return detection

    # ------------------------------------------------------------------ helper
    def _detect_package(
        self, context: DetectionContext, rel_path: str, detection: Detection
    ) -> None:
        data = context.json(rel_path)
        if not isinstance(data, dict):
            return
        package_dir = rel_path.rsplit("/", 1)[0] if "/" in rel_path else "."
        declared_name = str(data.get("name") or "")
        version = str(data.get("version") or "")
        private = bool(data.get("private"))
        manager = self._package_manager(context, data)
        evidence = [fact(rel_path, "package.json", weight=0.9)]

        detection.technologies.append(
            DetectedTechnology(
                kind=TechnologyKind.RUNTIME,
                name="Node.js",
                version=_node_range(data),
                certainty=Certainty.FACT,
                confidence=0.85,
                evidence=evidence,
            )
        )
        detection.technologies.append(
            DetectedTechnology(
                kind=TechnologyKind.PACKAGE_MANAGER,
                name=manager,
                certainty=Certainty.FACT,
                confidence=0.85,
                evidence=[fact(rel_path, "packageManager", weight=0.8)]
                if data.get("packageManager")
                else [fact(_lockfile_for(context, manager) or rel_path, weight=0.7)],
            )
        )

        all_deps: dict[str, tuple[str, DependencyScope, str]] = {}
        for field, scope in (
            ("dependencies", DependencyScope.RUNTIME),
            ("devDependencies", DependencyScope.DEVELOPMENT),
            ("optionalDependencies", DependencyScope.OPTIONAL),
            ("peerDependencies", DependencyScope.PEER),
        ):
            entries = data.get(field)
            if not isinstance(entries, dict):
                continue
            for index, (dep_name, dep_version) in enumerate(sorted(entries.items())):
                if index >= _DEPENDENCY_LIMIT:
                    detection.notes.append(
                        f"{rel_path}: {field} truncated at {_DEPENDENCY_LIMIT} entries"
                    )
                    break
                all_deps.setdefault(str(dep_name), (str(dep_version), scope, field))
                detection.dependencies.append(
                    Dependency(
                        ecosystem="npm",
                        name=str(dep_name),
                        version_spec=str(dep_version),
                        scope=scope,
                        manifest=rel_path,
                        evidence=[fact(rel_path, f"{field}.{dep_name}", weight=0.8)],
                    )
                )

        for dep_name, (_spec, _scope, field) in all_deps.items():
            hint = _FRAMEWORKS.get(dep_name.lower())
            if hint:
                detection.technologies.append(
                    DetectedTechnology(
                        kind=hint[1],
                        name=hint[0],
                        certainty=Certainty.FACT,
                        confidence=0.85,
                        evidence=[fact(rel_path, f"{field}.{dep_name}", weight=0.8)],
                    )
                )

        # ------------------------------------------------------------- commands
        scripts = data.get("scripts")
        lifecycle: list[tuple[str, str]] = []
        if isinstance(scripts, dict):
            for script_name in sorted(scripts):
                body = str(scripts[script_name] or "")
                if str(script_name).lower() in _LIFECYCLE_HOOKS:
                    lifecycle.append((str(script_name), body))
                    continue
                purpose = classify_purpose(str(script_name), body)
                detection.commands.append(
                    build_command(
                        script_command(manager, str(script_name)),
                        source=CommandSource.PACKAGE_SCRIPT,
                        path=rel_path,
                        locator=f"scripts.{script_name}",
                        purpose=purpose,
                        cwd=package_dir,
                        certainty=Certainty.FACT,
                        confidence=0.9,
                        detail=f"package.json script '{script_name}'",
                        snippet=body,
                        script_body=body,
                        component=slugify(declared_name.split("/")[-1])
                        if declared_name and package_dir not in (".", "")
                        else None,
                    )
                )
        if lifecycle:
            detection.notes.append(
                f"{rel_path}: lifecycle scripts run automatically on install: "
                f"{', '.join(name for name, _ in lifecycle)}"
            )
        for hook_name, hook_body in lifecycle:
            hook_risk = classify_command(hook_body)
            if hook_risk.is_dangerous:
                detection.risks.append(
                    Risk(
                        id=f"dangerous-lifecycle-{slugify(declared_name) or 'package'}-{hook_name}",
                        title=f"Install hook '{hook_name}' runs a destructive command",
                        description=(
                            f"The '{hook_name}' script in {rel_path} matches destructive or remote-code "
                            "patterns and runs automatically when dependencies are installed."
                        ),
                        severity=Severity.HIGH,
                        category=RiskCategory.SECURITY,
                        evidence=[fact(rel_path, f"scripts.{hook_name}", hook_body, weight=0.9)],
                        mitigation="Review the hook before running any install command in this repository.",
                    )
                )

        if "typescript" in all_deps and context.exists("tsconfig.json"):
            detection.commands.append(
                build_command(
                    f"{manager} run typecheck"
                    if "typecheck" in (scripts or {})
                    else "tsc --noEmit",
                    source=CommandSource.CONFIG,
                    path="tsconfig.json",
                    purpose=CommandPurpose.TYPECHECK,
                    cwd=package_dir,
                    certainty=Certainty.INFERENCE,
                    confidence=0.7,
                    detail="TypeScript project with tsconfig.json",
                )
            )

        self._detect_prisma(context, package_dir, detection)
        self._detect_workspaces(context, data, manager, detection, evidence)

        kind = ComponentKind.PACKAGE
        if private:
            kind = ComponentKind.APPLICATION
        if data.get("bin"):
            kind = ComponentKind.CLI
        technologies = sorted(
            {
                tech.name
                for tech in detection.technologies
                if tech.evidence and tech.evidence[0].source == rel_path
            }
        )
        detection.components.append(
            ProjectComponent(
                name=slugify(declared_name.split("/")[-1])
                if declared_name
                else _dir_name(package_dir),
                path=package_dir,
                kind=kind,
                ecosystems=["node"],
                technologies=technologies,
                manifests=[rel_path],
                evidence=[
                    Evidence(
                        kind=Certainty.FACT,
                        source=rel_path,
                        locator="name",
                        detail=f"{declared_name} {version}".strip(),
                        weight=0.9,
                    )
                ],
                confidence=0.85,
                certainty=Certainty.FACT,
            )
        )

        framework_names = {
            hint[0]
            for dep_name, hint in ((name, _FRAMEWORKS.get(name.lower())) for name in all_deps)
            if hint
        }
        if framework_names & {
            "Next.js",
            "Remix",
            "Nuxt",
            "Astro",
            "SvelteKit",
            "Express",
            "Fastify",
            "NestJS",
            "Hono",
        }:
            detection.apis.append(
                API(
                    kind=APIKind.REST,
                    framework=next(
                        (
                            name
                            for name in (
                                "Next.js",
                                "Remix",
                                "Nuxt",
                                "Astro",
                                "SvelteKit",
                                "Express",
                                "Fastify",
                                "NestJS",
                                "Hono",
                            )
                            if name in framework_names
                        ),
                        None,
                    ),
                    entrypoints=_next_entrypoints(context) if "Next.js" in framework_names else [],
                    router_dirs=[
                        directory
                        for directory in (
                            "app/api",
                            "pages/api",
                            "src/routes",
                            "routes",
                            "src/app/api",
                        )
                        if context.scan.has_directory(directory)
                    ],
                    evidence=[fact(rel_path, "dependencies", weight=0.7)],
                    certainty=Certainty.INFERENCE,
                    confidence=0.7,
                )
            )

    def _package_manager(self, context: DetectionContext, data: dict[str, Any]) -> str:
        declared = str(data.get("packageManager") or "")
        if declared:
            return declared.split("@", 1)[0]
        for lockfile, manager in _MANAGER_BY_LOCKFILE:
            if context.exists(lockfile):
                return manager
        return "npm"

    def _detect_prisma(
        self, context: DetectionContext, package_dir: str, detection: Detection
    ) -> None:
        schema_candidates = [
            path
            for path in ("prisma/schema.prisma", f"{package_dir}/prisma/schema.prisma")
            if context.exists(path)
        ]
        if not schema_candidates:
            return
        schema_path = schema_candidates[0]
        text = context.read(schema_path) or ""
        provider_match = re.search(r"provider\s*=\s*\"(?P<provider>[a-z0-9_]+)\"", text)
        engine = provider_match.group("provider") if provider_match else "unknown"
        detection.databases.append(
            Database(
                engine=_engine(engine),
                orm="Prisma",
                migration_tool="Prisma Migrate",
                migration_dirs=[
                    path
                    for path in (f"{package_dir}/prisma/migrations", "prisma/migrations")
                    if context.scan.has_directory(path)
                ],
                config_files=[schema_path],
                evidence=[fact(schema_path, "datasource.provider", weight=0.9)],
                certainty=Certainty.FACT,
                confidence=0.9,
            )
        )
        detection.technologies.append(
            DetectedTechnology(
                kind=TechnologyKind.DATABASE_TOOL,
                name="Prisma",
                certainty=Certainty.FACT,
                confidence=0.9,
                evidence=[fact(schema_path, weight=0.9)],
            )
        )

    def _detect_workspaces(
        self,
        context: DetectionContext,
        data: dict[str, Any],
        manager: str,
        detection: Detection,
        evidence: list[Evidence],
    ) -> None:
        workspaces = data.get("workspaces")
        patterns: list[str] = []
        if isinstance(workspaces, list):
            patterns = [str(item) for item in workspaces]
        elif isinstance(workspaces, dict):
            packages = workspaces.get("packages")
            if isinstance(packages, list):
                patterns = [str(item) for item in packages]
        if not patterns:
            return
        matchable = sorted(
            directory
            for directory in context.scan.directories
            if any(match_path(pattern, directory) for pattern in patterns)
        )
        if matchable:
            detection.technologies.append(
                DetectedTechnology(
                    kind=TechnologyKind.BUILD_TOOL,
                    name="Monorepo workspaces",
                    certainty=Certainty.FACT,
                    confidence=0.8,
                    evidence=evidence,
                    notes=[f"{len(matchable)} workspace package(s) matched"],
                )
            )
            detection.notes.append(
                f"npm/yarn workspaces: {', '.join(patterns)} (manager: {manager})"
            )


def _node_range(data: dict[str, Any]) -> str | None:
    engines = data.get("engines")
    if isinstance(engines, dict) and engines.get("node"):
        return str(engines["node"])
    return None


def _lockfile_for(context: DetectionContext, manager: str) -> str | None:
    for lockfile, candidate in _MANAGER_BY_LOCKFILE:
        if candidate == manager and context.exists(lockfile):
            return lockfile
    return None


def _dir_name(path: str) -> str:
    if path in (".", ""):
        return "app"
    return slugify(PurePosixPath(path).name)


def _engine(provider: str) -> Any:
    from skillforge.models.workflow import DatabaseEngine

    mapping = {
        "postgresql": DatabaseEngine.POSTGRESQL,
        "postgres": DatabaseEngine.POSTGRESQL,
        "mysql": DatabaseEngine.MYSQL,
        "sqlite": DatabaseEngine.SQLITE,
        "mongodb": DatabaseEngine.MONGODB,
        "sqlserver": DatabaseEngine.MSSQL,
    }
    return mapping.get(provider.lower(), DatabaseEngine.UNKNOWN)


def _next_entrypoints(context: DetectionContext) -> list[str]:
    candidates = ["app/page.tsx", "app/page.jsx", "pages/index.tsx", "pages/index.jsx"]
    return [path for path in candidates if context.exists(path)]
