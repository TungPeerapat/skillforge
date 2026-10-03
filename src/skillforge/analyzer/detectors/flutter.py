"""Flutter / Dart ecosystem detector."""

from __future__ import annotations

from typing import Any, Final

from skillforge.analyzer.detectors.base import Detection, DetectionContext, fact
from skillforge.discovery.commands import build_command
from skillforge.models import (
    Certainty,
    ComponentKind,
    Database,
    Dependency,
    DependencyScope,
    DetectedTechnology,
    ProjectComponent,
    TechnologyKind,
)
from skillforge.models.workflow import CommandPurpose, CommandSource, DatabaseEngine
from skillforge.utils.text import slugify

_PACKAGE_TECH: Final[dict[str, tuple[str, TechnologyKind]]] = {
    "provider": ("Provider", TechnologyKind.LIBRARY),
    "riverpod": ("Riverpod", TechnologyKind.LIBRARY),
    "flutter_riverpod": ("Riverpod", TechnologyKind.LIBRARY),
    "bloc": ("Bloc", TechnologyKind.LIBRARY),
    "flutter_bloc": ("Bloc", TechnologyKind.LIBRARY),
    "get": ("GetX", TechnologyKind.LIBRARY),
    "mobx": ("MobX", TechnologyKind.LIBRARY),
    "dio": ("Dio", TechnologyKind.LIBRARY),
    "http": ("http", TechnologyKind.LIBRARY),
    "go_router": ("go_router", TechnologyKind.LIBRARY),
    "auto_route": ("auto_route", TechnologyKind.LIBRARY),
    "hive": ("Hive", TechnologyKind.LIBRARY),
    "sqflite": ("sqflite", TechnologyKind.LIBRARY),
    "drift": ("Drift", TechnologyKind.LIBRARY),
    "isar": ("Isar", TechnologyKind.LIBRARY),
    "firebase_core": ("Firebase", TechnologyKind.CLOUD),
    "firebase_auth": ("Firebase Auth", TechnologyKind.CLOUD),
    "cloud_firestore": ("Cloud Firestore", TechnologyKind.DATABASE),
    "google_maps_flutter": ("Google Maps", TechnologyKind.LIBRARY),
    "intl": ("intl", TechnologyKind.LIBRARY),
    "flutter_localizations": ("Flutter localizations", TechnologyKind.LIBRARY),
}

_PLATFORM_BUILDS: Final[tuple[tuple[str, str], ...]] = (
    ("android", "apk"),
    ("ios", "ios"),
    ("web", "web"),
    ("windows", "windows"),
    ("macos", "macos"),
    ("linux", "linux"),
)


class FlutterDetector:
    """Detects Flutter applications, packages, and platform targets."""

    id = "flutter"

    def applies(self, context: DetectionContext) -> bool:
        return context.exists("pubspec.yaml")

    def detect(self, context: DetectionContext) -> Detection:
        detection = Detection()
        data = context.yaml("pubspec.yaml")
        if not isinstance(data, dict):
            return detection
        dependencies = data.get("dependencies")
        dev_dependencies = data.get("dev_dependencies")
        dependency_map: dict[str, Any] = {}
        if isinstance(dependencies, dict):
            dependency_map.update(dependencies)
        if isinstance(dev_dependencies, dict):
            dependency_map.update(dev_dependencies)

        is_flutter = "flutter" in {str(key).lower() for key in dependency_map} or (
            isinstance(dependencies, dict) and "sdk" in str(dependencies.get("flutter", {})).lower()
        )
        sdk_constraint = None
        environment = data.get("environment")
        if isinstance(environment, dict):
            sdk_constraint = str(environment.get("sdk") or "") or None
        flutter_constraint = None
        if isinstance(environment, dict):
            flutter_constraint = str(environment.get("flutter") or "") or None

        detection.technologies.append(
            DetectedTechnology(
                kind=TechnologyKind.LANGUAGE,
                name="Dart",
                version=sdk_constraint,
                certainty=Certainty.FACT,
                confidence=0.9,
                evidence=[fact("pubspec.yaml", "environment.sdk", weight=0.85)],
            )
        )
        if is_flutter or context.scan.has_directory("android", "ios", "lib"):
            detection.technologies.append(
                DetectedTechnology(
                    kind=TechnologyKind.FRAMEWORK,
                    name="Flutter",
                    version=flutter_constraint,
                    certainty=Certainty.FACT if is_flutter else Certainty.INFERENCE,
                    confidence=0.85 if is_flutter else 0.6,
                    evidence=[fact("pubspec.yaml", "dependencies.flutter", weight=0.85)]
                    if is_flutter
                    else [fact("pubspec.yaml", weight=0.5)],
                )
            )

        for name, spec in sorted(dependency_map.items()):
            lower = str(name).lower()
            version = None
            if isinstance(spec, str):
                version = spec
            elif isinstance(spec, dict):
                version = str(spec.get("version") or "") or None
            scope = (
                DependencyScope.DEVELOPMENT
                if isinstance(dev_dependencies, dict) and name in dev_dependencies
                else DependencyScope.RUNTIME
            )
            detection.dependencies.append(
                Dependency(
                    ecosystem="pub",
                    name=lower,
                    version_spec=version,
                    scope=scope,
                    manifest="pubspec.yaml",
                    evidence=[fact("pubspec.yaml", f"dependencies.{lower}", weight=0.8)],
                )
            )
            hint = _PACKAGE_TECH.get(lower)
            if hint:
                detection.technologies.append(
                    DetectedTechnology(
                        kind=hint[1],
                        name=hint[0],
                        certainty=Certainty.FACT,
                        confidence=0.8,
                        evidence=[fact("pubspec.yaml", f"dependencies.{lower}", weight=0.75)],
                    )
                )

        detection.commands.extend(
            [
                build_command(
                    "flutter pub get",
                    source=CommandSource.CONFIG,
                    path="pubspec.yaml",
                    purpose=CommandPurpose.SETUP,
                    certainty=Certainty.INFERENCE,
                    confidence=0.85,
                    detail="pubspec.yaml detected",
                ),
                build_command(
                    "flutter analyze",
                    source=CommandSource.CONFIG,
                    path="analysis_options.yaml"
                    if context.exists("analysis_options.yaml")
                    else "pubspec.yaml",
                    purpose=CommandPurpose.LINT,
                    certainty=Certainty.INFERENCE,
                    confidence=0.8,
                    detail="standard Flutter static analysis",
                ),
                build_command(
                    "flutter test",
                    source=CommandSource.CONFIG,
                    path="pubspec.yaml",
                    purpose=CommandPurpose.TEST,
                    certainty=Certainty.INFERENCE,
                    confidence=0.8,
                    detail="Flutter widget/unit tests",
                ),
                build_command(
                    "dart format --output=none --set-exit-if-changed .",
                    source=CommandSource.CONFIG,
                    path="pubspec.yaml",
                    purpose=CommandPurpose.FORMAT,
                    certainty=Certainty.INFERENCE,
                    confidence=0.65,
                    detail="Dart formatting check",
                ),
            ]
        )
        if context.exists("lib/main.dart"):
            detection.commands.append(
                build_command(
                    "flutter run",
                    source=CommandSource.CONFIG,
                    path="lib/main.dart",
                    purpose=CommandPurpose.RUN,
                    certainty=Certainty.INFERENCE,
                    confidence=0.8,
                    detail="Flutter application entry point detected",
                    notes=["select a device/emulator with 'flutter devices' first"],
                )
            )
        platforms = [
            target
            for directory, target in _PLATFORM_BUILDS
            if context.scan.has_directory(directory)
        ]
        for target in platforms:
            detection.commands.append(
                build_command(
                    f"flutter build {target}",
                    source=CommandSource.CONFIG,
                    path="pubspec.yaml",
                    purpose=CommandPurpose.BUILD,
                    certainty=Certainty.INFERENCE,
                    confidence=0.7,
                    detail=f"platform directory for '{target}' detected",
                )
            )

        if any(name in dependency_map for name in ("sqflite", "drift", "isar", "hive")):
            detection.databases.append(
                Database(
                    engine=DatabaseEngine.SQLITE,
                    orm=next(
                        (
                            name
                            for name in ("drift", "sqflite", "isar", "hive")
                            if name in dependency_map
                        ),
                        None,
                    ),
                    evidence=[fact("pubspec.yaml", weight=0.7)],
                    certainty=Certainty.INFERENCE,
                    confidence=0.7,
                )
            )
        if "firebase_core" in dependency_map:
            detection.notes.append(
                "Firebase is configured; some commands/checks require local emulators or credentials."
            )

        detection.components.append(
            ProjectComponent(
                name=slugify(str(data.get("name") or "flutter-app")),
                path=".",
                kind=ComponentKind.APPLICATION if platforms else ComponentKind.PACKAGE,
                ecosystems=["dart"],
                technologies=sorted({tech.name for tech in detection.technologies}),
                manifests=["pubspec.yaml"],
                evidence=[fact("pubspec.yaml", "name", weight=0.9)],
                confidence=0.9,
                certainty=Certainty.FACT,
            )
        )
        return detection
