"""Go ecosystem detector."""

from __future__ import annotations

import re
from typing import Final

from skillforge.analyzer.detectors.base import Detection, DetectionContext, fact
from skillforge.discovery.commands import build_command
from skillforge.models import (
    API,
    Certainty,
    ComponentKind,
    Database,
    Dependency,
    DependencyScope,
    DetectedTechnology,
    ProjectComponent,
    TechnologyKind,
)
from skillforge.models.workflow import APIKind, CommandPurpose, CommandSource, DatabaseEngine
from skillforge.utils.text import slugify

_FRAMEWORKS: Final[dict[str, tuple[str, TechnologyKind]]] = {
    "github.com/gin-gonic/gin": ("Gin", TechnologyKind.FRAMEWORK),
    "github.com/labstack/echo": ("Echo", TechnologyKind.FRAMEWORK),
    "github.com/labstack/echo/v4": ("Echo", TechnologyKind.FRAMEWORK),
    "github.com/gofiber/fiber": ("Fiber", TechnologyKind.FRAMEWORK),
    "github.com/gofiber/fiber/v2": ("Fiber", TechnologyKind.FRAMEWORK),
    "github.com/go-chi/chi": ("chi", TechnologyKind.FRAMEWORK),
    "github.com/go-chi/chi/v5": ("chi", TechnologyKind.FRAMEWORK),
    "github.com/gorilla/mux": ("gorilla/mux", TechnologyKind.FRAMEWORK),
    "gorm.io/gorm": ("GORM", TechnologyKind.LIBRARY),
    "entgo.io/ent": ("Ent", TechnologyKind.LIBRARY),
    "github.com/jmoiron/sqlx": ("sqlx", TechnologyKind.LIBRARY),
    "github.com/spf13/cobra": ("Cobra", TechnologyKind.LIBRARY),
    "github.com/stretchr/testify": ("testify", TechnologyKind.TEST_FRAMEWORK),
    "go.uber.org/zap": ("Zap", TechnologyKind.LIBRARY),
    "github.com/rs/zerolog": ("zerolog", TechnologyKind.LIBRARY),
    "github.com/redis/go-redis": ("Redis", TechnologyKind.CACHE),
    "github.com/lib/pq": ("PostgreSQL", TechnologyKind.DATABASE),
    "github.com/jackc/pgx": ("PostgreSQL", TechnologyKind.DATABASE),
    "gorm.io/driver/postgres": ("PostgreSQL", TechnologyKind.DATABASE),
    "gorm.io/driver/mysql": ("MySQL", TechnologyKind.DATABASE),
    "gorm.io/driver/sqlite": ("SQLite", TechnologyKind.DATABASE),
    "go.mongodb.org/mongo-driver": ("MongoDB", TechnologyKind.DATABASE),
}

_REQUIRE_LINE_RE = re.compile(
    r"^\s*(?P<name>[^\s]+)\s+v(?P<version>[^\s]+)(?P<indirect>\s*//\s*indirect)?"
)
_MODULE_RE = re.compile(r"^module\s+(?P<module>\S+)", re.MULTILINE)
_GO_VERSION_RE = re.compile(r"^go\s+(?P<version>\S+)", re.MULTILINE)

_API_FRAMEWORKS = {"Gin", "Echo", "Fiber", "chi", "gorilla/mux"}


class GoDetector:
    """Detects Go modules, frameworks, and commands."""

    id = "go"

    def applies(self, context: DetectionContext) -> bool:
        return context.exists("go.mod")

    def detect(self, context: DetectionContext) -> Detection:
        detection = Detection()
        text = context.read("go.mod") or ""
        module_match = _MODULE_RE.search(text)
        go_version = _GO_VERSION_RE.search(text)
        module_name = module_match.group("module") if module_match else ""

        detection.technologies.append(
            DetectedTechnology(
                kind=TechnologyKind.LANGUAGE,
                name="Go",
                version=go_version.group("version") if go_version else None,
                certainty=Certainty.FACT,
                confidence=0.9,
                evidence=[fact("go.mod", "go", weight=0.9)],
            )
        )

        dependencies: list[tuple[str, str, bool]] = []
        in_require = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("require ("):
                in_require = True
                continue
            if in_require and stripped == ")":
                in_require = False
                continue
            if stripped.startswith("require "):
                stripped = stripped[len("require ") :]
            elif not in_require:
                continue
            match = _REQUIRE_LINE_RE.match(stripped)
            if not match:
                continue
            name = match.group("name")
            version = match.group("version")
            indirect = bool(match.group("indirect"))
            dependencies.append((name, version, indirect))
            detection.dependencies.append(
                Dependency(
                    ecosystem="go",
                    name=name,
                    version_spec=f"v{version}",
                    scope=DependencyScope.OPTIONAL if indirect else DependencyScope.RUNTIME,
                    manifest="go.mod",
                    evidence=[fact("go.mod", name, weight=0.8)],
                )
            )

        framework_names: set[str] = set()
        for name, _version, _indirect in dependencies:
            hint = _FRAMEWORKS.get(name)
            if not hint:
                continue
            display, kind = hint
            framework_names.add(display)
            detection.technologies.append(
                DetectedTechnology(
                    kind=kind,
                    name=display,
                    certainty=Certainty.FACT,
                    confidence=0.85,
                    evidence=[fact("go.mod", name, weight=0.8)],
                )
            )

        has_tests = bool(context.records_with_suffix("_test.go"))
        has_main = self._has_main_package(context)

        detection.commands.append(
            build_command(
                "go mod download",
                source=CommandSource.CONFIG,
                path="go.mod",
                purpose=CommandPurpose.SETUP,
                certainty=Certainty.INFERENCE,
                confidence=0.75,
                detail="Go module file detected",
            )
        )
        if has_tests:
            detection.commands.append(
                build_command(
                    "go test ./...",
                    source=CommandSource.CONFIG,
                    path="go.mod",
                    purpose=CommandPurpose.TEST,
                    certainty=Certainty.INFERENCE,
                    confidence=0.85,
                    detail="Go test files detected",
                )
            )
        if has_main:
            detection.commands.append(
                build_command(
                    "go build ./...",
                    source=CommandSource.CONFIG,
                    path="go.mod",
                    purpose=CommandPurpose.BUILD,
                    certainty=Certainty.INFERENCE,
                    confidence=0.8,
                    detail="main package detected",
                )
            )
            detection.commands.append(
                build_command(
                    "go run .",
                    source=CommandSource.CONFIG,
                    path="go.mod",
                    purpose=CommandPurpose.RUN,
                    certainty=Certainty.INFERENCE,
                    confidence=0.7,
                    detail="main package detected at module root"
                    if context.exists("main.go")
                    else "main package detected; adjust the package path if it is not the root",
                )
            )
        detection.commands.append(
            build_command(
                "go vet ./...",
                source=CommandSource.CONFIG,
                path="go.mod",
                purpose=CommandPurpose.LINT,
                certainty=Certainty.INFERENCE,
                confidence=0.7,
                detail="standard Go vet check",
            )
        )

        if framework_names & _API_FRAMEWORKS:
            detection.apis.append(
                API(
                    kind=APIKind.REST,
                    framework=sorted(framework_names & _API_FRAMEWORKS)[0],
                    router_dirs=[
                        directory
                        for directory in (
                            "internal/handlers",
                            "internal/api",
                            "handlers",
                            "api",
                            "routes",
                            "internal/router",
                        )
                        if context.scan.has_directory(directory)
                    ],
                    evidence=[fact("go.mod", weight=0.7)],
                    certainty=Certainty.INFERENCE,
                    confidence=0.7,
                )
            )

        engine = DatabaseEngine.UNKNOWN
        if framework_names & {"PostgreSQL"}:
            engine = DatabaseEngine.POSTGRESQL
        elif framework_names & {"MySQL"}:
            engine = DatabaseEngine.MYSQL
        elif framework_names & {"SQLite"}:
            engine = DatabaseEngine.SQLITE
        elif framework_names & {"MongoDB"}:
            engine = DatabaseEngine.MONGODB
        if engine is not DatabaseEngine.UNKNOWN or framework_names & {"GORM", "Ent", "sqlx"}:
            orm = next((name for name in ("GORM", "Ent", "sqlx") if name in framework_names), None)
            detection.databases.append(
                Database(
                    engine=engine,
                    orm=orm,
                    evidence=[fact("go.mod", weight=0.7)],
                    certainty=Certainty.INFERENCE,
                    confidence=0.65,
                )
            )

        module_leaf = module_name.split("/")[-1] if module_name else "go"
        detection.components.append(
            ProjectComponent(
                name=slugify(module_leaf) or "go",
                path=".",
                kind=ComponentKind.APPLICATION if has_main else ComponentKind.LIBRARY,
                ecosystems=["go"],
                technologies=sorted({tech.name for tech in detection.technologies}),
                manifests=["go.mod"],
                evidence=[fact("go.mod", "module", weight=0.9)],
                confidence=0.9,
                certainty=Certainty.FACT,
            )
        )
        return detection

    def _has_main_package(self, context: DetectionContext) -> bool:
        for record in context.source_files():
            if not record.path.endswith(".go"):
                continue
            text = context.read(record.path)
            if text and re.search(r"^package\s+main\b", text, re.MULTILINE):
                return True
        return False
