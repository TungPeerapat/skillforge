"""Python ecosystem detector.

Handles monorepos: every directory containing a Python manifest is analysed as
its own project and contributes a component.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Final

from skillforge.analyzer.detectors.base import Detection, DetectionContext, fact
from skillforge.analyzer.detectors.manifests import (
    database_engine_hint,
    framework_hint,
    own_version,
    parse_requirements_text,
    split_specifier,
)
from skillforge.analyzer.scanner import FileRecord
from skillforge.discovery.commands import build_command
from skillforge.models import (
    API,
    Certainty,
    Command,
    ComponentKind,
    Database,
    Dependency,
    DependencyScope,
    DetectedTechnology,
    Evidence,
    ProjectComponent,
    TechnologyKind,
    Unknown,
    confidence_from_evidence,
)
from skillforge.models.workflow import APIKind, CommandPurpose, CommandSource, DatabaseEngine
from skillforge.utils.text import slugify

_WEB_FRAMEWORKS: Final[dict[str, str]] = {
    "fastapi": "FastAPI",
    "django": "Django",
    "flask": "Flask",
    "starlette": "Starlette",
    "litestar": "Litestar",
    "aiohttp": "aiohttp",
    "sanic": "Sanic",
    "tornado": "Tornado",
}

_FASTAPI_APP_RE = re.compile(r"^\s*(?P<name>app|application)\s*=\s*FastAPI\s*\(", re.MULTILINE)
_FLASK_APP_RE = re.compile(r"^\s*(?P<name>app|application)\s*=\s*Flask\s*\(", re.MULTILINE)
_UVICORN_RUN_RE = re.compile(r"uvicorn\.run\(\s*[\"'](?P<target>[A-Za-z0-9_.:]+)[\"']")

_ENTRYPOINT_FILE_HINTS: Final[tuple[str, ...]] = (
    "main.py",
    "app.py",
    "server.py",
    "asgi.py",
    "wsgi.py",
    "api.py",
    "__init__.py",
)

_PYTHON_MANIFEST_NAMES: Final[tuple[str, ...]] = (
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "requirements.txt",
    "requirements-dev.txt",
    "requirements-test.txt",
    "Pipfile",
    "poetry.lock",
    "uv.lock",
    "pdm.lock",
)


class PythonDetector:
    """Detects Python projects, frameworks, databases, and test tooling."""

    id = "python"

    def applies(self, context: DetectionContext) -> bool:
        if context.records_named(*_PYTHON_MANIFEST_NAMES):
            return True
        return bool(context.records_with_suffix(".py"))

    def detect(self, context: DetectionContext) -> Detection:
        detection = Detection()
        directories = sorted(
            {record.parent for record in context.records_named(*_PYTHON_MANIFEST_NAMES)}
        )
        if not directories:
            return detection
        for base in directories:
            self._detect_project(context, base, detection)
        return detection

    # ------------------------------------------------------------------ project
    def _detect_project(self, context: DetectionContext, base: str, detection: Detection) -> None:
        pyproject_path = _join(base, "pyproject.toml")
        pyproject = context.toml(pyproject_path)
        dependencies: list[tuple[str, str | None, DependencyScope, str]] = []
        console_scripts: list[str] = []

        requires_python: str | None = None
        declared_name: str | None = None
        build_backend: str | None = None
        uses_poetry = False
        uses_uv = False
        evidence: list[Evidence] = []

        if pyproject:
            evidence.append(fact(pyproject_path, "project", weight=0.9))
            project = pyproject.get("project")
            if isinstance(project, dict):
                declared_name = str(project.get("name") or "") or None
                requires_python = own_version(project.get("requires-python"))
                for raw in project.get("dependencies") or []:
                    name, version = split_specifier(str(raw))
                    if name:
                        dependencies.append(
                            (
                                name,
                                version,
                                DependencyScope.RUNTIME,
                                f"{pyproject_path}:project.dependencies",
                            )
                        )
                optional = project.get("optional-dependencies")
                if isinstance(optional, dict):
                    for group, entries in optional.items():
                        scope = (
                            DependencyScope.DEVELOPMENT
                            if "dev" in str(group).lower()
                            else DependencyScope.OPTIONAL
                        )
                        for raw in entries or []:
                            name, version = split_specifier(str(raw))
                            if name:
                                dependencies.append(
                                    (
                                        name,
                                        version,
                                        scope,
                                        f"{pyproject_path}:optional-dependencies.{group}",
                                    )
                                )
                scripts = project.get("scripts")
                if isinstance(scripts, dict):
                    console_scripts.extend(sorted(str(key) for key in scripts))
            groups = pyproject.get("dependency-groups")
            if isinstance(groups, dict):
                for group, entries in groups.items():
                    scope = (
                        DependencyScope.DEVELOPMENT
                        if "dev" in str(group).lower()
                        else DependencyScope.OPTIONAL
                    )
                    for raw in entries or []:
                        if isinstance(raw, dict):  # {include-group = "dev"}
                            continue
                        name, version = split_specifier(str(raw))
                        if name:
                            dependencies.append(
                                (
                                    name,
                                    version,
                                    scope,
                                    f"{pyproject_path}:dependency-groups.{group}",
                                )
                            )
            build_system = pyproject.get("build-system")
            if isinstance(build_system, dict):
                build_backend = str(build_system.get("build-backend") or "") or None
            tool = pyproject.get("tool") if isinstance(pyproject.get("tool"), dict) else {}
            poetry = tool.get("poetry") if isinstance(tool, dict) else None
            if isinstance(poetry, dict):
                uses_poetry = True
                declared_name = declared_name or (
                    str(poetry.get("name")) if poetry.get("name") else None
                )
                poetry_deps = poetry.get("dependencies") or {}
                if isinstance(poetry_deps, dict):
                    for dep_name, spec in poetry_deps.items():
                        if dep_name.lower() == "python":
                            requires_python = requires_python or own_version(spec)
                            continue
                        if isinstance(spec, list):
                            spec = {"version": spec[0] if spec else None, "optional": True}
                        version = own_version(spec) if isinstance(spec, dict) else str(spec)
                        optional = bool(spec.get("optional")) if isinstance(spec, dict) else False
                        dependencies.append(
                            (
                                str(dep_name),
                                version,
                                DependencyScope.OPTIONAL if optional else DependencyScope.RUNTIME,
                                f"{pyproject_path}:tool.poetry.dependencies",
                            )
                        )
                dev_group = (
                    poetry.get("group", {}).get("dev", {})
                    if isinstance(poetry.get("group"), dict)
                    else {}
                )
                dev_deps = dev_group.get("dependencies", {}) if isinstance(dev_group, dict) else {}
                if isinstance(dev_deps, dict):
                    for dep_name, spec in dev_deps.items():
                        version = own_version(spec) if isinstance(spec, dict) else str(spec)
                        dependencies.append(
                            (
                                str(dep_name),
                                version,
                                DependencyScope.DEVELOPMENT,
                                f"{pyproject_path}:tool.poetry.group.dev",
                            )
                        )
            if isinstance(tool, dict) and isinstance(tool.get("uv"), dict):
                uses_uv = True
            if isinstance(tool, dict) and isinstance(tool.get("pytest"), dict):
                dependencies.append(
                    ("pytest", None, DependencyScope.DEVELOPMENT, f"{pyproject_path}:tool.pytest")
                )

        includes_uv_lock = context.exists(_join(base, "uv.lock"))
        includes_poetry_lock = context.exists(_join(base, "poetry.lock"))
        uses_uv = uses_uv or includes_uv_lock

        for record in _requirements_records(context, base):
            text = context.read(record.path)
            if not text:
                continue
            scope = (
                DependencyScope.DEVELOPMENT
                if any(token in record.name.lower() for token in ("dev", "test"))
                else DependencyScope.RUNTIME
            )
            for name, version in parse_requirements_text(text):
                dependencies.append((name, version, scope, record.path))
            evidence.append(fact(record.path, weight=0.8))

        version_file = _join(base, ".python-version")
        python_version = None
        if context.exists(version_file):
            declared = (context.read(version_file) or "").strip().splitlines()
            if declared:
                python_version = declared[0].strip()
                evidence.append(fact(version_file, "L1", weight=0.7))

        technologies: list[DetectedTechnology] = []
        technologies.append(
            DetectedTechnology(
                kind=TechnologyKind.LANGUAGE,
                name="Python",
                version=python_version or requires_python,
                certainty=Certainty.FACT if evidence else Certainty.INFERENCE,
                confidence=confidence_from_evidence(evidence, base=0.6),
                evidence=evidence or [fact("python-source-files", weight=0.5)],
                notes=["requires-python: " + requires_python] if requires_python else [],
            )
        )
        if uses_uv:
            technologies.append(
                DetectedTechnology(
                    kind=TechnologyKind.PACKAGE_MANAGER,
                    name="uv",
                    certainty=Certainty.FACT,
                    confidence=0.9,
                    evidence=[
                        fact(_join(base, "uv.lock"), weight=0.9)
                        if includes_uv_lock
                        else fact(pyproject_path, "tool.uv", weight=0.7)
                    ],
                )
            )
        elif uses_poetry or includes_poetry_lock:
            technologies.append(
                DetectedTechnology(
                    kind=TechnologyKind.PACKAGE_MANAGER,
                    name="Poetry",
                    certainty=Certainty.FACT,
                    confidence=0.85,
                    evidence=[
                        fact(_join(base, "poetry.lock"), weight=0.85)
                        if includes_poetry_lock
                        else fact(pyproject_path, "tool.poetry", weight=0.75)
                    ],
                )
            )
        elif context.exists(_join(base, "Pipfile")):
            technologies.append(
                DetectedTechnology(
                    kind=TechnologyKind.PACKAGE_MANAGER,
                    name="Pipenv",
                    certainty=Certainty.FACT,
                    confidence=0.8,
                    evidence=[fact(_join(base, "Pipfile"), weight=0.8)],
                )
            )
        if build_backend:
            detection.notes.append(f"{base}: build backend {build_backend}")

        name_lookup: dict[str, tuple[str, str | None, DependencyScope, str]] = {}
        for name, version, scope, locator in dependencies:
            name_lookup.setdefault(name.lower(), (name, version, scope, locator))
        for name, version, scope, locator in name_lookup.values():
            detection.dependencies.append(
                Dependency(
                    ecosystem="pypi",
                    name=name,
                    version_spec=version,
                    scope=scope,
                    manifest=locator.split(":", 1)[0],
                    evidence=[fact(locator, f"dependency {name}", weight=0.7)],
                )
            )
            hint = framework_hint(name)
            if hint:
                display, kind_name = hint
                try:
                    technology_kind = TechnologyKind(kind_name)
                except ValueError:  # pragma: no cover - hint table is covered by tests
                    technology_kind = TechnologyKind.LIBRARY
                technologies.append(
                    DetectedTechnology(
                        kind=technology_kind,
                        name=display,
                        version=version,
                        certainty=Certainty.FACT,
                        confidence=0.85,
                        evidence=[fact(locator, weight=0.75)],
                    )
                )

        component_path = base
        component_name = (
            slugify(declared_name) if declared_name else (_dir_name(component_path) or "python")
        )

        commands = self._commands_for_project(
            context, base, pyproject_path, name_lookup, technologies, detection
        )
        # Commands discovered for this project belong to its component; this keeps
        # monorepo workflows separated by service. Root-level projects stay
        # repo-wide so Makefile/CI commands for the same project merge cleanly.
        command_component = component_name if base not in (".", "") else None
        commands = [
            command
            if command.component
            else command.model_copy(update={"component": command_component})
            for command in commands
        ]

        web_framework = next((name for name in _WEB_FRAMEWORKS if name in name_lookup), None)
        kind = ComponentKind.LIBRARY
        if web_framework:
            kind = ComponentKind.APPLICATION
        elif console_scripts:
            kind = ComponentKind.CLI
        manifests = sorted(
            record.path
            for record in context.scan.manifests()
            if record.path in {_join(base, name) for name in _PYTHON_MANIFEST_NAMES}
            or (record.parent == base and record.name.startswith("requirements"))
        )
        detection.components.append(
            ProjectComponent(
                name=component_name,
                path=component_path,
                kind=kind,
                ecosystems=["python"],
                technologies=sorted({tech.name for tech in technologies}),
                manifests=manifests,
                evidence=[fact(manifests[0], weight=0.8)]
                if manifests
                else [fact(component_path, weight=0.4)],
                confidence=0.8,
                certainty=Certainty.FACT,
            )
        )
        detection.technologies.extend(technologies)
        detection.commands.extend(commands)

        if requires_python is None and python_version is None:
            detection.unknowns.append(
                Unknown(
                    id=f"python-version-{slugify(component_name)}",
                    question=f"Which Python version does '{component_name}' target?",
                    why="No requires-python, .python-version, or toolchain file was found.",
                    suggested_check="Ask the maintainers or check the CI configuration.",
                    evidence=[fact(pyproject_path if pyproject else component_path, weight=0.4)],
                )
            )

    # ----------------------------------------------------------------- commands
    def _commands_for_project(
        self,
        context: DetectionContext,
        base: str,
        pyproject_path: str,
        name_lookup: dict[str, tuple[str, str | None, DependencyScope, str]],
        technologies: list[DetectedTechnology],
        detection: Detection,
    ) -> list[Command]:
        commands: list[Command] = []
        pyproject = context.toml(pyproject_path)
        includes_uv_lock = context.exists(_join(base, "uv.lock"))
        uses_uv = includes_uv_lock or (
            isinstance(pyproject, dict) and isinstance(pyproject.get("tool", {}).get("uv"), dict)
        )
        uses_poetry = context.exists(_join(base, "poetry.lock")) or (
            isinstance(pyproject, dict)
            and isinstance(pyproject.get("tool", {}).get("poetry"), dict)
        )
        pyproject_rel = pyproject_path

        if uses_uv:
            commands.append(
                build_command(
                    "uv sync",
                    source=CommandSource.CONFIG,
                    path=_join(base, "uv.lock") if includes_uv_lock else pyproject_rel,
                    locator="tool.uv",
                    purpose=CommandPurpose.SETUP,
                    cwd=base,
                    certainty=Certainty.INFERENCE,
                    confidence=0.75,
                    detail="uv lockfile or configuration detected",
                )
            )
        if uses_poetry:
            commands.append(
                build_command(
                    "poetry install",
                    source=CommandSource.CONFIG,
                    path=_join(base, "poetry.lock")
                    if context.exists(_join(base, "poetry.lock"))
                    else pyproject_rel,
                    purpose=CommandPurpose.SETUP,
                    cwd=base,
                    certainty=Certainty.INFERENCE,
                    confidence=0.75,
                    detail="Poetry configuration detected",
                )
            )
        for record in _requirements_records(context, base):
            commands.append(
                build_command(
                    f"pip install -r {record.name}",
                    source=CommandSource.CONFIG,
                    path=record.path,
                    purpose=CommandPurpose.SETUP,
                    cwd=base,
                    certainty=Certainty.INFERENCE,
                    confidence=0.7,
                    detail="requirements file detected",
                )
            )
        if pyproject and not uses_poetry and not uses_uv:
            commands.append(
                build_command(
                    "pip install -e .",
                    source=CommandSource.CONFIG,
                    path=pyproject_rel,
                    locator="project",
                    purpose=CommandPurpose.SETUP,
                    cwd=base,
                    certainty=Certainty.INFERENCE,
                    confidence=0.6,
                    detail="installable Python project detected",
                )
            )

        tests_present = any(
            record.path.startswith(f"{base}/tests/") or record.name.startswith("test_")
            for record in context.source_files()
            if record.suffix == ".py"
        ) or context.exists(_join(base, "conftest.py"))
        if "pytest" in name_lookup or context.exists(_join(base, "pytest.ini")) or tests_present:
            source = name_lookup["pytest"][3] if "pytest" in name_lookup else _join(base, "tests")
            commands.append(
                build_command(
                    "pytest",
                    source=CommandSource.CONFIG,
                    path=source,
                    purpose=CommandPurpose.TEST,
                    cwd=base,
                    certainty=Certainty.INFERENCE,
                    confidence=0.8,
                    detail="pytest detected",
                )
            )
        if "ruff" in name_lookup or (
            isinstance(pyproject, dict) and isinstance(pyproject.get("tool", {}).get("ruff"), dict)
        ):
            commands.append(
                build_command(
                    "ruff check .",
                    source=CommandSource.CONFIG,
                    path=pyproject_rel,
                    locator="tool.ruff",
                    purpose=CommandPurpose.LINT,
                    cwd=base,
                    certainty=Certainty.INFERENCE,
                    confidence=0.75,
                    detail="Ruff configured",
                )
            )
        if "mypy" in name_lookup or (
            isinstance(pyproject, dict) and isinstance(pyproject.get("tool", {}).get("mypy"), dict)
        ):
            commands.append(
                build_command(
                    "mypy .",
                    source=CommandSource.CONFIG,
                    path=pyproject_rel,
                    locator="tool.mypy",
                    purpose=CommandPurpose.TYPECHECK,
                    cwd=base,
                    certainty=Certainty.INFERENCE,
                    confidence=0.7,
                    detail="mypy configured",
                    notes=[
                        "pass the package directory instead of '.' if the project uses a src layout"
                    ],
                )
            )
        if "black" in name_lookup:
            commands.append(
                build_command(
                    "black --check .",
                    source=CommandSource.CONFIG,
                    path=pyproject_rel,
                    locator="dependencies.black",
                    purpose=CommandPurpose.FORMAT,
                    cwd=base,
                    certainty=Certainty.INFERENCE,
                    confidence=0.7,
                    detail="Black detected",
                )
            )

        web_framework = next((name for name in _WEB_FRAMEWORKS if name in name_lookup), None)
        app_target = self._find_app_target(context, base, web_framework) if web_framework else None
        api: API | None = None
        if web_framework == "fastapi":
            target = app_target or "app.main:app"
            commands.append(
                build_command(
                    f"uvicorn {target}",
                    source=CommandSource.CONFIG,
                    path=pyproject_rel,
                    locator="dependencies.fastapi",
                    purpose=CommandPurpose.RUN,
                    cwd=base,
                    certainty=Certainty.INFERENCE,
                    confidence=0.7 if app_target else 0.5,
                    detail="FastAPI detected; ASGI server is the standard way to run it",
                    notes=[
                        "The --reload flag is commonly used during development; confirm it matches your setup."
                    ],
                )
            )
            api = API(
                kind=APIKind.REST,
                framework="FastAPI",
                entrypoints=[target],
                router_dirs=[
                    directory
                    for directory in ("app/routers", "app/api", "routers", "api")
                    if context.scan.has_directory(_join(base, directory))
                ],
                spec_paths=[
                    path
                    for path in ("openapi.json", "openapi.yaml", "api/openapi.json")
                    if context.exists(_join(base, path))
                ],
                evidence=[fact(pyproject_rel, "dependencies.fastapi", weight=0.85)],
                certainty=Certainty.INFERENCE,
                confidence=0.75,
            )
        elif web_framework == "flask":
            commands.append(
                build_command(
                    "flask run",
                    source=CommandSource.CONFIG,
                    path=pyproject_rel,
                    locator="dependencies.flask",
                    purpose=CommandPurpose.RUN,
                    cwd=base,
                    certainty=Certainty.INFERENCE,
                    confidence=0.6,
                    detail="Flask detected",
                    notes=["set FLASK_APP if the application entry point is not auto-detected"],
                )
            )
            api = API(
                kind=APIKind.REST,
                framework="Flask",
                evidence=[fact(pyproject_rel, "dependencies.flask", weight=0.8)],
                certainty=Certainty.INFERENCE,
            )
        elif web_framework == "django" or context.exists(_join(base, "manage.py")):
            manage_path = _join(base, "manage.py")
            manage_text = context.read(manage_path) or ""
            if "django" in manage_text.lower() or web_framework == "django":
                commands.extend(
                    [
                        build_command(
                            "python manage.py runserver",
                            source=CommandSource.CONFIG,
                            path=manage_path,
                            purpose=CommandPurpose.RUN,
                            cwd=base,
                            certainty=Certainty.INFERENCE,
                            confidence=0.85,
                            detail="Django manage.py detected",
                        ),
                        build_command(
                            "python manage.py migrate",
                            source=CommandSource.CONFIG,
                            path=manage_path,
                            purpose=CommandPurpose.MIGRATE,
                            cwd=base,
                            certainty=Certainty.INFERENCE,
                            confidence=0.8,
                            detail="Django migrations",
                        ),
                        build_command(
                            "python manage.py test",
                            source=CommandSource.CONFIG,
                            path=manage_path,
                            purpose=CommandPurpose.TEST,
                            cwd=base,
                            certainty=Certainty.INFERENCE,
                            confidence=0.8,
                            detail="Django test runner",
                        ),
                    ]
                )
                api = API(
                    kind=APIKind.REST,
                    framework="Django",
                    entrypoints=[manage_path],
                    evidence=[fact(manage_path, weight=0.85)],
                    certainty=Certainty.FACT,
                    confidence=0.85,
                )
                detection.databases.append(
                    Database(
                        engine=DatabaseEngine.POSTGRESQL
                        if "psycopg" in name_lookup
                        else DatabaseEngine.UNKNOWN,
                        orm="Django ORM",
                        migration_tool="django",
                        evidence=[fact(manage_path, weight=0.7)],
                        certainty=Certainty.INFERENCE,
                        confidence=0.6,
                    )
                )

        alembic_ini = _join(base, "alembic.ini")
        alembic_dir = _join(base, "alembic")
        if context.exists(alembic_ini) or context.scan.has_directory(alembic_dir):
            migration_dirs = [
                path
                for path in sorted(context.scan.directories)
                if (path == alembic_dir or path.startswith(f"{alembic_dir}/"))
                or PurePosixPath(path).name in {"alembic", "versions"}
            ]
            source_path = (
                alembic_ini
                if context.exists(alembic_ini)
                else (migration_dirs[0] if migration_dirs else alembic_ini)
            )
            commands.extend(
                [
                    build_command(
                        "alembic upgrade head",
                        source=CommandSource.CONFIG,
                        path=source_path,
                        purpose=CommandPurpose.MIGRATE,
                        cwd=base,
                        certainty=Certainty.INFERENCE,
                        confidence=0.8,
                        detail="Alembic migrations detected",
                        notes=["applies migrations to the configured database"],
                    ),
                    build_command(
                        "alembic current",
                        source=CommandSource.CONFIG,
                        path=source_path,
                        purpose=CommandPurpose.VERIFY,
                        cwd=base,
                        certainty=Certainty.INFERENCE,
                        confidence=0.7,
                        detail="read-only migration state check",
                    ),
                ]
            )
            detection.databases.append(
                Database(
                    engine=self._engine_from_dependencies(name_lookup) or DatabaseEngine.UNKNOWN,
                    orm="SQLAlchemy" if "sqlalchemy" in name_lookup else None,
                    migration_tool="Alembic",
                    migration_dirs=migration_dirs,
                    config_files=[alembic_ini] if context.exists(alembic_ini) else [],
                    evidence=[fact(source_path, weight=0.85)],
                    certainty=Certainty.FACT,
                    confidence=0.85,
                )
            )
        elif "sqlalchemy" in name_lookup:
            detection.databases.append(
                Database(
                    engine=self._engine_from_dependencies(name_lookup) or DatabaseEngine.UNKNOWN,
                    orm="SQLAlchemy",
                    evidence=[
                        fact(name_lookup["sqlalchemy"][3], "dependency sqlalchemy", weight=0.7)
                    ],
                    certainty=Certainty.FACT,
                    confidence=0.6,
                )
            )

        if api:
            detection.apis.append(api)
        self._detect_migrations_dir(context, base, detection)
        _ = technologies  # technologies are extended by the caller
        return commands

    def _detect_migrations_dir(
        self, context: DetectionContext, base: str, detection: Detection
    ) -> None:
        directory = _join(base, "migrations")
        if not context.scan.has_directory(directory) or "alembic" in directory:
            return
        if any(item.migration_dirs for item in detection.databases):
            return
        detection.databases.append(
            Database(
                engine=DatabaseEngine.UNKNOWN,
                migration_tool="migrations directory",
                migration_dirs=[directory],
                evidence=[fact(directory, weight=0.6)],
                certainty=Certainty.INFERENCE,
                confidence=0.6,
            )
        )

    # ------------------------------------------------------------------ helpers
    def _engine_from_dependencies(
        self, lookup: dict[str, tuple[str, str | None, DependencyScope, str]]
    ) -> DatabaseEngine | None:
        for name in lookup:
            engine = database_engine_hint(name)
            if engine:
                try:
                    return DatabaseEngine(engine)
                except ValueError:  # pragma: no cover - hint table is validated by tests
                    continue
        return None

    def _find_app_target(
        self, context: DetectionContext, base: str, framework: str | None
    ) -> str | None:
        if framework not in {"fastapi", "flask"}:
            return None
        pattern = _FASTAPI_APP_RE if framework == "fastapi" else _FLASK_APP_RE
        prefix = f"{base}/" if base not in (".", "") else ""
        candidates = [
            record
            for record in context.source_files()
            if record.path.endswith(".py")
            and record.path.startswith(prefix)
            and record.path in context.scan.contents
        ]
        candidates.sort(key=lambda record: (record.name not in _ENTRYPOINT_FILE_HINTS, record.path))
        for record in candidates[:400]:
            text = context.read(record.path) or ""
            match = pattern.search(text)
            if not match:
                continue
            module = _module_name(record.path[len(prefix) :])
            if module:
                return f"{module}:{match.group('name')}"
            run_match = _UVICORN_RUN_RE.search(text)
            if run_match:
                return run_match.group("target")
        return None


def _join(base: str, name: str) -> str:
    return name if base in (".", "") else f"{base}/{name}"


def _requirements_records(context: DetectionContext, base: str) -> list[FileRecord]:
    return sorted(
        (
            record
            for record in context.scan.files
            if record.parent == base
            and record.name.startswith("requirements")
            and record.name.endswith(".txt")
        ),
        key=lambda record: record.path,
    )


def _module_name(path: str) -> str:
    without_suffix = path[: -len(".py")] if path.endswith(".py") else path
    parts = [part for part in without_suffix.split("/") if part]
    if parts and parts[0] == "src" and len(parts) > 1:
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _dir_name(path: str) -> str | None:
    if path in (".", ""):
        return None
    return slugify(PurePosixPath(path).name)
