"""Java/Kotlin ecosystem detector (Maven and Gradle)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
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

_DEPENDENCY_HINTS: Final[dict[str, tuple[str, TechnologyKind]]] = {
    "spring-boot-starter-web": ("Spring Boot", TechnologyKind.FRAMEWORK),
    "spring-boot-starter-data-jpa": ("Spring Data JPA", TechnologyKind.LIBRARY),
    "spring-boot-starter-security": ("Spring Security", TechnologyKind.LIBRARY),
    "spring-boot-starter-test": ("Spring Boot Test", TechnologyKind.TEST_FRAMEWORK),
    "quarkus-resteasy": ("Quarkus", TechnologyKind.FRAMEWORK),
    "quarkus-rest": ("Quarkus", TechnologyKind.FRAMEWORK),
    "micronaut-http-server": ("Micronaut", TechnologyKind.FRAMEWORK),
    "junit-jupiter": ("JUnit 5", TechnologyKind.TEST_FRAMEWORK),
    "junit": ("JUnit", TechnologyKind.TEST_FRAMEWORK),
    "mockito-core": ("Mockito", TechnologyKind.TEST_FRAMEWORK),
    "postgresql": ("PostgreSQL", TechnologyKind.DATABASE),
    "mysql-connector-j": ("MySQL", TechnologyKind.DATABASE),
    "h2": ("H2", TechnologyKind.DATABASE),
    "liquibase-core": ("Liquibase", TechnologyKind.DATABASE_TOOL),
    "flyway-core": ("Flyway", TechnologyKind.DATABASE_TOOL),
}

_ENGINE_HINTS: Final[dict[str, DatabaseEngine]] = {
    "postgresql": DatabaseEngine.POSTGRESQL,
    "mysql-connector-j": DatabaseEngine.MYSQL,
    "h2": DatabaseEngine.UNKNOWN,
    "mariadb-java-client": DatabaseEngine.MARIADB,
    "sqlite-jdbc": DatabaseEngine.SQLITE,
    "mongodb-driver-sync": DatabaseEngine.MONGODB,
}

_GRADLE_DEP_RE = re.compile(
    r"""^(?P<scope>implementation|api|compileOnly|runtimeOnly|testImplementation|annotationProcessor)\s*[( ]\s*['"](?P<group>[\w.\-]+):(?P<artifact>[\w.\-]+)(?::(?P<version>[^'"]+))?['"]""",
    re.MULTILINE,
)


class JavaDetector:
    """Detects Maven/Gradle projects and their standard commands."""

    id = "java"

    def applies(self, context: DetectionContext) -> bool:
        return bool(
            context.exists("pom.xml")
            or context.exists("build.gradle")
            or context.exists("build.gradle.kts")
            or context.records_with_suffix(".java", ".kt")
        )

    def detect(self, context: DetectionContext) -> Detection:
        detection = Detection()
        dependencies: dict[str, str] = {}
        java_version: str | None = None

        if context.exists("pom.xml"):
            self._detect_maven(context, detection, dependencies)
            pom_text = context.read("pom.xml") or ""
            version_match = re.search(r"<java\.version>([^<]+)</java\.version>", pom_text)
            if version_match:
                java_version = version_match.group(1).strip()

        for gradle_path in ("build.gradle", "build.gradle.kts"):
            if context.exists(gradle_path):
                text = context.read(gradle_path) or ""
                for match in _GRADLE_DEP_RE.finditer(text):
                    artifact = match.group("artifact")
                    dependencies.setdefault(artifact, match.group("version") or "")
                    detection.dependencies.append(
                        Dependency(
                            ecosystem="maven",
                            name=f"{match.group('group')}:{artifact}",
                            version_spec=match.group("version"),
                            scope=DependencyScope.RUNTIME
                            if "test" not in match.group("scope")
                            else DependencyScope.DEVELOPMENT,
                            manifest=gradle_path,
                            evidence=[
                                fact(gradle_path, f"{match.group('scope')} {artifact}", weight=0.8)
                            ],
                        )
                    )
                toolchain = re.search(
                    r"(?:sourceCompatibility|languageVersion)\s*=?\s*[\"']?(?:JavaLanguageVersion\.of\()?[\"']?(\d+)",
                    text,
                )
                if toolchain:
                    java_version = java_version or toolchain.group(1)
                if "org.springframework.boot" in text:
                    detection.technologies.append(
                        DetectedTechnology(
                            kind=TechnologyKind.FRAMEWORK,
                            name="Spring Boot",
                            certainty=Certainty.FACT,
                            confidence=0.9,
                            evidence=[
                                fact(gradle_path, "plugins org.springframework.boot", weight=0.85)
                            ],
                        )
                    )
                detection.technologies.append(
                    DetectedTechnology(
                        kind=TechnologyKind.BUILD_TOOL,
                        name="Gradle",
                        certainty=Certainty.FACT,
                        confidence=0.9,
                        evidence=[fact(gradle_path, weight=0.85)],
                    )
                )

        for artifact, version in sorted(dependencies.items()):
            hint = _DEPENDENCY_HINTS.get(artifact)
            if hint:
                detection.technologies.append(
                    DetectedTechnology(
                        kind=hint[1],
                        name=hint[0],
                        version=version or None,
                        certainty=Certainty.FACT,
                        confidence=0.8,
                        evidence=[
                            fact(
                                "pom.xml" if context.exists("pom.xml") else "build.gradle",
                                f"dependency {artifact}",
                                weight=0.75,
                            )
                        ],
                    )
                )

        detection.technologies.append(
            DetectedTechnology(
                kind=TechnologyKind.LANGUAGE,
                name="Java",
                version=java_version,
                certainty=Certainty.FACT if java_version else Certainty.INFERENCE,
                confidence=0.85 if java_version else 0.6,
                evidence=[
                    fact("pom.xml" if context.exists("pom.xml") else "build.gradle", weight=0.8)
                ],
            )
        )

        maven = context.exists("pom.xml")
        wrapper = "mvnw" if maven else "gradlew"
        wrapper_path = "mvnw" if maven else "gradlew"
        use_wrapper = context.exists(wrapper_path)
        base = f"./{wrapper}" if use_wrapper else ("mvn" if maven else "gradle")
        wrapper_note = (
            ["on Windows use mvnw.cmd/gradlew.bat instead of ./mvnw/./gradlew"]
            if use_wrapper and not context.scan.has_directory()
            else ["on Windows use mvnw.cmd/gradlew.bat instead of ./mvnw/./gradlew"]
            if use_wrapper
            else []
        )
        build_file = (
            "pom.xml"
            if maven
            else ("build.gradle.kts" if context.exists("build.gradle.kts") else "build.gradle")
        )

        detection.commands.append(
            build_command(
                f"{base} test",
                source=CommandSource.CONFIG,
                path=build_file,
                purpose=CommandPurpose.TEST,
                certainty=Certainty.INFERENCE,
                confidence=0.8,
                detail="standard test task",
                notes=wrapper_note,
            )
        )
        if not maven:
            detection.commands.append(
                build_command(
                    f"{base} build",
                    source=CommandSource.CONFIG,
                    path=build_file,
                    purpose=CommandPurpose.BUILD,
                    certainty=Certainty.INFERENCE,
                    confidence=0.8,
                    detail="standard Gradle build task",
                    notes=wrapper_note,
                )
            )
            if any(tech.name == "Spring Boot" for tech in detection.technologies):
                detection.commands.append(
                    build_command(
                        f"{base} bootRun",
                        source=CommandSource.CONFIG,
                        path=build_file,
                        purpose=CommandPurpose.RUN,
                        certainty=Certainty.INFERENCE,
                        confidence=0.75,
                        detail="Spring Boot Gradle plugin detected",
                        notes=wrapper_note,
                    )
                )
        else:
            detection.commands.append(
                build_command(
                    f"{base} package",
                    source=CommandSource.CONFIG,
                    path=build_file,
                    purpose=CommandPurpose.BUILD,
                    certainty=Certainty.INFERENCE,
                    confidence=0.8,
                    detail="standard Maven package goal",
                    notes=wrapper_note,
                )
            )
            if any(tech.name == "Spring Boot" for tech in detection.technologies):
                detection.commands.append(
                    build_command(
                        f"{base} spring-boot:run",
                        source=CommandSource.CONFIG,
                        path=build_file,
                        purpose=CommandPurpose.RUN,
                        certainty=Certainty.INFERENCE,
                        confidence=0.75,
                        detail="Spring Boot Maven plugin detected",
                        notes=wrapper_note,
                    )
                )

        if any(tech.name == "Spring Boot" for tech in detection.technologies):
            detection.apis.append(
                API(
                    kind=APIKind.REST,
                    framework="Spring Boot",
                    router_dirs=[
                        directory
                        for directory in (
                            "src/main/java",
                            "src/main/kotlin",
                        )
                        if context.scan.has_directory(directory)
                    ],
                    evidence=[fact(build_file, weight=0.7)],
                    certainty=Certainty.INFERENCE,
                    confidence=0.7,
                )
            )

        migration_tool = next(
            (
                name
                for name in ("Flyway", "Liquibase")
                if any(tech.name == name for tech in detection.technologies)
            ),
            None,
        )
        engine = next(
            (_ENGINE_HINTS[name] for name in dependencies if name in _ENGINE_HINTS),
            DatabaseEngine.UNKNOWN,
        )
        if engine is not DatabaseEngine.UNKNOWN or migration_tool:
            migration_dirs = [
                directory
                for directory in sorted(context.scan.directories)
                if any(
                    part in {"migration", "migrations", "db", "changelog"}
                    for part in directory.split("/")
                )
            ]
            detection.databases.append(
                Database(
                    engine=engine,
                    migration_tool=migration_tool,
                    migration_dirs=migration_dirs,
                    config_files=[
                        path
                        for path in (
                            "src/main/resources/application.yml",
                            "src/main/resources/application.properties",
                        )
                        if context.exists(path)
                    ],
                    evidence=[fact(build_file, weight=0.7)],
                    certainty=Certainty.INFERENCE,
                    confidence=0.65,
                )
            )

        detection.components.append(
            ProjectComponent(
                name=slugify(_artifact_id(context) or "java"),
                path=".",
                kind=ComponentKind.APPLICATION,
                ecosystems=["java"],
                technologies=sorted({tech.name for tech in detection.technologies}),
                manifests=[
                    path
                    for path in (
                        "pom.xml",
                        "build.gradle",
                        "build.gradle.kts",
                        "settings.gradle",
                        "settings.gradle.kts",
                    )
                    if context.exists(path)
                ],
                evidence=[fact(build_file, weight=0.85)],
                confidence=0.85,
                certainty=Certainty.FACT,
            )
        )
        return detection

    def _detect_maven(
        self, context: DetectionContext, detection: Detection, dependencies: dict[str, str]
    ) -> None:
        text = context.read("pom.xml")
        if not text:
            return
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return
        namespace = ""
        if root.tag.startswith("{"):
            namespace = root.tag[: root.tag.index("}") + 1]
        for element in root.iter(f"{namespace}dependency"):
            artifact = (element.findtext(f"{namespace}artifactId") or "").strip()
            version = (element.findtext(f"{namespace}version") or "").strip()
            if artifact:
                dependencies.setdefault(artifact, version)
                detection.dependencies.append(
                    Dependency(
                        ecosystem="maven",
                        name=artifact,
                        version_spec=version or None,
                        scope=DependencyScope.RUNTIME,
                        manifest="pom.xml",
                        evidence=[fact("pom.xml", f"dependency {artifact}", weight=0.8)],
                    )
                )
        detection.technologies.append(
            DetectedTechnology(
                kind=TechnologyKind.BUILD_TOOL,
                name="Maven",
                certainty=Certainty.FACT,
                confidence=0.9,
                evidence=[fact("pom.xml", weight=0.85)],
            )
        )


def _artifact_id(context: DetectionContext) -> str | None:
    text = context.read("pom.xml")
    if not text:
        return None
    match = re.search(r"<artifactId>([^<]+)</artifactId>", text)
    return match.group(1).strip() if match else None
