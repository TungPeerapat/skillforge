""".NET ecosystem detector (C#, VB.NET, F#)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import PurePosixPath
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

_MSBUILD_NS = "{http://schemas.microsoft.com/developer/msbuild/2003}"

_PACKAGE_TECH: Final[dict[str, tuple[str, TechnologyKind]]] = {
    "microsoft.entityframeworkcore": ("Entity Framework Core", TechnologyKind.DATABASE_TOOL),
    "npgsql.entityframeworkcore.postgresql": ("PostgreSQL", TechnologyKind.DATABASE),
    "microsoft.entityframeworkcore.sqlserver": ("SQL Server", TechnologyKind.DATABASE),
    "microsoft.entityframeworkcore.sqlite": ("SQLite", TechnologyKind.DATABASE),
    "pomelo.entityframeworkcore.mysql": ("MySQL", TechnologyKind.DATABASE),
    "microsoft.aspnetcore.app": ("ASP.NET Core", TechnologyKind.FRAMEWORK),
    "swashbuckle.aspnetcore": ("Swashbuckle", TechnologyKind.LIBRARY),
    "serilog": ("Serilog", TechnologyKind.LIBRARY),
    "xunit": ("xUnit", TechnologyKind.TEST_FRAMEWORK),
    "xunit.v3": ("xUnit", TechnologyKind.TEST_FRAMEWORK),
    "nunit": ("NUnit", TechnologyKind.TEST_FRAMEWORK),
    "mstest.testframework": ("MSTest", TechnologyKind.TEST_FRAMEWORK),
    "fluentassertions": ("FluentAssertions", TechnologyKind.TEST_FRAMEWORK),
    "moq": ("Moq", TechnologyKind.TEST_FRAMEWORK),
    "automapper": ("AutoMapper", TechnologyKind.LIBRARY),
    "mediatr": ("MediatR", TechnologyKind.LIBRARY),
    "microsoft.extensions.hosting": ("Generic Host", TechnologyKind.LIBRARY),
}

_EF_ENGINE: Final[dict[str, DatabaseEngine]] = {
    "npgsql.entityframeworkcore.postgresql": DatabaseEngine.POSTGRESQL,
    "microsoft.entityframeworkcore.sqlserver": DatabaseEngine.MSSQL,
    "microsoft.entityframeworkcore.sqlite": DatabaseEngine.SQLITE,
    "pomelo.entityframeworkcore.mysql": DatabaseEngine.MYSQL,
}

#: Prefix rules applied when no exact package mapping exists.
_PACKAGE_PREFIX_TECH: Final[tuple[tuple[str, str, TechnologyKind], ...]] = (
    ("microsoft.entityframeworkcore", "Entity Framework Core", TechnologyKind.DATABASE_TOOL),
    ("npgsql.entityframeworkcore", "PostgreSQL", TechnologyKind.DATABASE),
    ("pomelo.entityframeworkcore", "MySQL", TechnologyKind.DATABASE),
    ("microsoft.aspnetcore", "ASP.NET Core", TechnologyKind.FRAMEWORK),
    ("xunit", "xUnit", TechnologyKind.TEST_FRAMEWORK),
    ("nunit", "NUnit", TechnologyKind.TEST_FRAMEWORK),
)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(element: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in element if _local_name(child.tag) == name]


def _text(element: ET.Element, name: str) -> str | None:
    for child in _children(element, name):
        if child.text and child.text.strip():
            return child.text.strip()
    return None


class DotnetDetector:
    """Detects .NET solutions, projects, frameworks, and commands."""

    id = "dotnet"

    def applies(self, context: DetectionContext) -> bool:
        return bool(context.records_with_suffix(".csproj", ".vbproj", ".fsproj", ".sln", ".slnf"))

    def detect(self, context: DetectionContext) -> Detection:
        detection = Detection()
        projects = context.records_with_suffix(".csproj", ".vbproj", ".fsproj")
        solutions = context.records_with_suffix(".sln", ".slnf")
        if solutions:
            detection.technologies.append(
                DetectedTechnology(
                    kind=TechnologyKind.BUILD_TOOL,
                    name="MSBuild solution",
                    certainty=Certainty.FACT,
                    confidence=0.8,
                    evidence=[fact(solutions[0].path, weight=0.8)],
                )
            )

        has_test_project = False
        has_executable = False
        has_web = False
        packages: dict[str, str] = {}
        target_framework: str | None = None
        language = "csharp"
        for record in sorted(projects, key=lambda item: item.path):
            language = {
                ".vbproj": "vbnet",
                ".fsproj": "fsharp",
            }.get(record.suffix, "csharp")
            root = self._parse_project(context, record.path)
            if root is None:
                continue
            is_web = (root.get("Sdk") or "").lower().endswith(".web")
            output_type = (_text(root, "OutputType") or "").lower()
            if output_type in {"exe", "winexe"}:
                has_executable = True
            if is_web or output_type == "exe":
                has_executable = True
            has_web = has_web or is_web
            if is_web:
                detection.technologies.append(
                    DetectedTechnology(
                        kind=TechnologyKind.FRAMEWORK,
                        name="ASP.NET Core",
                        certainty=Certainty.FACT,
                        confidence=0.9,
                        evidence=[fact(record.path, "Sdk Microsoft.NET.Sdk.Web", weight=0.85)],
                    )
                )
            framework = _text(root, "TargetFramework") or _text(root, "TargetFrameworks")
            target_framework = target_framework or framework
            for group in _children(root, "ItemGroup"):
                for reference in _children(group, "PackageReference"):
                    name = (reference.get("Include") or "").strip()
                    if not name:
                        continue
                    version = reference.get("Version") or _text(reference, "Version") or ""
                    packages.setdefault(name.lower(), version)
                    detection.dependencies.append(
                        Dependency(
                            ecosystem="nuget",
                            name=name,
                            version_spec=version or None,
                            scope=DependencyScope.RUNTIME,
                            manifest=record.path,
                            evidence=[fact(record.path, f"PackageReference {name}", weight=0.8)],
                        )
                    )
            for group in _children(root, "ItemGroup"):
                for reference in _children(group, "FrameworkReference"):
                    name = (reference.get("Include") or "").lower()
                    if "aspnetcore" in name:
                        has_web = True
                        detection.technologies.append(
                            DetectedTechnology(
                                kind=TechnologyKind.FRAMEWORK,
                                name="ASP.NET Core",
                                certainty=Certainty.FACT,
                                confidence=0.9,
                                evidence=[fact(record.path, "FrameworkReference", weight=0.85)],
                            )
                        )
            if "test" in record.name.lower():
                has_test_project = True

        detection.technologies.append(
            DetectedTechnology(
                kind=TechnologyKind.RUNTIME,
                name=".NET",
                version=target_framework,
                certainty=Certainty.FACT if target_framework else Certainty.INFERENCE,
                confidence=0.85,
                evidence=[fact(projects[0].path, "TargetFramework", weight=0.85)]
                if projects and target_framework
                else [fact(solutions[0].path, weight=0.7)]
                if solutions
                else [fact(projects[0].path, weight=0.6)]
                if projects
                else [],
            )
        )
        for package, version in sorted(packages.items()):
            hint = _PACKAGE_TECH.get(package)
            if hint is None:
                prefix = next(
                    (item for item in _PACKAGE_PREFIX_TECH if package.startswith(item[0])), None
                )
                if prefix is not None:
                    hint = (prefix[1], prefix[2])
            if hint:
                evidence_path = next(
                    (
                        record.path
                        for record in projects
                        if package in (context.read(record.path) or "").lower()
                    ),
                    projects[0].path if projects else (solutions[0].path if solutions else ""),
                )
                detection.technologies.append(
                    DetectedTechnology(
                        kind=hint[1],
                        name=hint[0],
                        version=version or None,
                        certainty=Certainty.FACT,
                        confidence=0.85,
                        evidence=[fact(evidence_path, f"PackageReference {package}", weight=0.8)],
                    )
                )
        if any(name in packages for name in ("xunit", "xunit.v3", "nunit", "mstest.testframework")):
            has_test_project = True

        # ------------------------------------------------------------- commands
        build_target = solutions[0].path if solutions else (projects[0].path if projects else "")
        detection.commands.append(
            build_command(
                "dotnet restore",
                source=CommandSource.CONFIG,
                path=build_target,
                purpose=CommandPurpose.SETUP,
                certainty=Certainty.INFERENCE,
                confidence=0.75,
                detail=".NET solution detected",
            )
        )
        detection.commands.append(
            build_command(
                "dotnet build",
                source=CommandSource.CONFIG,
                path=build_target,
                purpose=CommandPurpose.BUILD,
                certainty=Certainty.INFERENCE,
                confidence=0.8,
                detail=".NET solution detected",
            )
        )
        if has_test_project:
            detection.commands.append(
                build_command(
                    "dotnet test",
                    source=CommandSource.CONFIG,
                    path=build_target,
                    purpose=CommandPurpose.TEST,
                    certainty=Certainty.INFERENCE,
                    confidence=0.85,
                    detail="test project detected",
                )
            )
        if has_executable:
            primary = _primary_project(context, projects)
            detection.commands.append(
                build_command(
                    f"dotnet run --project {primary}",
                    source=CommandSource.CONFIG,
                    path=primary,
                    purpose=CommandPurpose.RUN,
                    certainty=Certainty.INFERENCE,
                    confidence=0.75,
                    detail="executable project detected",
                    notes=[
                        "ports and environment come from launchSettings.json / appsettings.json"
                    ],
                )
            )
        if context.exists(".editorconfig"):
            detection.commands.append(
                build_command(
                    "dotnet format --verify-no-changes",
                    source=CommandSource.CONFIG,
                    path=".editorconfig",
                    purpose=CommandPurpose.FORMAT,
                    certainty=Certainty.INFERENCE,
                    confidence=0.65,
                    detail=".editorconfig detected",
                )
            )

        has_ef = any(name.startswith("microsoft.entityframeworkcore") for name in packages)
        migration_dirs = [
            directory
            for directory in sorted(context.scan.directories)
            if PurePosixPath(directory).name.lower() in {"migrations", "migration"}
        ]
        if has_ef:
            detection.commands.append(
                build_command(
                    "dotnet ef database update",
                    source=CommandSource.CONFIG,
                    path=build_target,
                    purpose=CommandPurpose.MIGRATE,
                    certainty=Certainty.INFERENCE,
                    confidence=0.7,
                    detail="Entity Framework Core detected",
                    notes=["requires the dotnet-ef tool and a configured connection string"],
                )
            )
            engine = next(
                (_EF_ENGINE[name] for name in packages if name in _EF_ENGINE),
                DatabaseEngine.UNKNOWN,
            )
            detection.databases.append(
                Database(
                    engine=engine,
                    orm="Entity Framework Core",
                    migration_tool="dotnet ef",
                    migration_dirs=migration_dirs,
                    evidence=[
                        fact(build_target, "PackageReference EntityFrameworkCore", weight=0.8)
                    ],
                    certainty=Certainty.FACT,
                    confidence=0.8,
                )
            )
        if has_web:
            detection.apis.append(
                API(
                    kind=APIKind.REST,
                    framework="ASP.NET Core",
                    entrypoints=[_primary_project(context, projects)],
                    router_dirs=[
                        directory
                        for directory in ("Controllers", "endpoints", "Api", "src/Controllers")
                        if context.scan.has_directory(directory)
                    ],
                    spec_paths=[
                        path for path in ("openapi.json", "swagger.json") if context.exists(path)
                    ],
                    evidence=[fact(build_target, weight=0.8)],
                    certainty=Certainty.FACT,
                    confidence=0.8,
                )
            )
            if "swashbuckle.aspnetcore" in packages:
                detection.notes.append(
                    "Swashbuckle is present: a Swagger UI is usually available in development."
                )

        detection.components.append(
            ProjectComponent(
                name=_component_name(context, solutions, projects),
                path=_component_path(projects, solutions),
                kind=ComponentKind.APPLICATION if has_executable else ComponentKind.LIBRARY,
                ecosystems=["dotnet"],
                technologies=sorted({tech.name for tech in detection.technologies}),
                manifests=sorted([record.path for record in [*solutions, *projects]]),
                evidence=[fact(build_target, weight=0.85)],
                confidence=0.85,
                certainty=Certainty.FACT,
            )
        )
        if language != "csharp":
            detection.technologies.append(
                DetectedTechnology(
                    kind=TechnologyKind.LANGUAGE,
                    name="VB.NET" if language == "vbnet" else "F#",
                    certainty=Certainty.FACT,
                    confidence=0.8,
                    evidence=[fact(projects[0].path, weight=0.8)],
                )
            )
        return detection

    def _parse_project(self, context: DetectionContext, path: str) -> ET.Element | None:
        text = context.read(path)
        if not text:
            return None
        try:
            return ET.fromstring(text)
        except ET.ParseError:
            return None


def _primary_project(context: DetectionContext, projects: list) -> str:
    if not projects:
        return ""
    for record in projects:
        text = (context.read(record.path) or "").lower()
        if "<outputtype>exe</outputtype>" in text or 'sdk="microsoft.net.sdk.web"' in text:
            return record.path
    return projects[0].path


def _component_name(context: DetectionContext, solutions: list, projects: list) -> str:
    """Name the component after the solution when one exists (single unit)."""
    if solutions:
        return slugify(PurePosixPath(solutions[0].path).stem)
    if projects:
        return slugify(PurePosixPath(projects[0].path).stem)
    return "dotnet"


def _component_path(projects: list, solutions: list) -> str:
    if solutions:
        return solutions[0].parent
    if projects:
        return projects[0].parent
    return "."
