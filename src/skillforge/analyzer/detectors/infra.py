"""Container, Makefile, Taskfile, and orchestration detector."""

from __future__ import annotations

import re
from typing import Any, Final

from skillforge.analyzer.detectors.base import Detection, DetectionContext, fact
from skillforge.discovery.commands import (
    build_command,
    makefile_commands,
    procfile_commands,
    taskfile_commands,
)
from skillforge.models import (
    Certainty,
    DetectedTechnology,
    Risk,
    Service,
    Severity,
    TechnologyKind,
)
from skillforge.models.workflow import (
    CommandPurpose,
    CommandSource,
    RiskCategory,
    ServiceKind,
    ServiceOrigin,
)
from skillforge.utils.text import slugify

_COMPOSE_FILES: Final[tuple[str, ...]] = (
    "compose.yml",
    "compose.yaml",
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.override.yml",
    "compose.override.yaml",
    "docker-compose.override.yml",
    "docker-compose.override.yaml",
)

_DB_IMAGE_HINTS: Final[tuple[tuple[str, str], ...]] = (
    ("postgres", "postgresql"),
    ("mysql", "mysql"),
    ("mariadb", "mariadb"),
    ("mongo", "mongodb"),
    ("mssql", "mssql"),
    ("clickhouse", "clickhouse"),
    ("elasticsearch", "elasticsearch"),
    ("neo4j", "neo4j"),
    ("oracle", "oracle"),
)

_CACHE_IMAGE_HINTS: Final[tuple[str, ...]] = ("redis", "memcached", "valkey", "keydb")
_QUEUE_IMAGE_HINTS: Final[tuple[str, ...]] = ("rabbitmq", "kafka", "nats", "zookeeper", "activemq")
_STORAGE_IMAGE_HINTS: Final[tuple[str, ...]] = ("minio", "seaweedfs", "azurite", "fake-gcs-server")

_FROM_RE = re.compile(
    r"^FROM\s+(?:--platform=\S+\s+)?(?P<image>\S+)(?:\s+AS\s+(?P<stage>\S+))?",
    re.MULTILINE | re.IGNORECASE,
)
_EXPOSE_RE = re.compile(r"^EXPOSE\s+(?P<ports>[^\n#]+)", re.MULTILINE | re.IGNORECASE)
_ENV_RE = re.compile(r"^ENV\s+(?P<body>[^\n]+)", re.MULTILINE | re.IGNORECASE)
_CMD_RE = re.compile(r"^(?:CMD|ENTRYPOINT)\s+(?P<body>[^\n]+)", re.MULTILINE | re.IGNORECASE)


class InfraDetector:
    """Detects Docker, Compose, Make/Task, Procfile, Kubernetes, and Terraform."""

    id = "infra"

    def applies(self, context: DetectionContext) -> bool:
        return bool(
            context.records_named(
                *(
                    *_COMPOSE_FILES,
                    "Makefile",
                    "makefile",
                    "Taskfile.yml",
                    "Taskfile.yaml",
                    "Procfile",
                    "justfile",
                )
            )
            or context.records_with_suffix(".tf")
            or context.scan.directories_matching("k8s")
        )

    def detect(self, context: DetectionContext) -> Detection:
        detection = Detection()
        self._detect_compose(context, detection)
        self._detect_dockerfiles(context, detection)
        self._detect_makefiles(context, detection)
        self._detect_taskfiles(context, detection)
        self._detect_procfiles(context, detection)
        self._detect_orchestration(context, detection)
        return detection

    # ------------------------------------------------------------------ compose
    def _detect_compose(self, context: DetectionContext, detection: Detection) -> None:
        for path in _COMPOSE_FILES:
            if not context.exists(path):
                continue
            data = context.yaml(path)
            if not isinstance(data, dict):
                continue
            services = data.get("services")
            detection.technologies.append(
                DetectedTechnology(
                    kind=TechnologyKind.CONTAINER,
                    name="Docker Compose",
                    certainty=Certainty.FACT if data else Certainty.INFERENCE,
                    confidence=0.9,
                    evidence=[fact(path, "services", weight=0.9)],
                )
            )
            if not isinstance(services, dict) or not services:
                continue
            for name in sorted(services):
                spec = services[name] if isinstance(services[name], dict) else {}
                image = str(spec.get("image") or "") or None
                build = spec.get("build")
                kind = _service_kind(name, image)
                ports = [str(port) for port in (spec.get("ports") or []) if port is not None]
                environment_keys = _environment_keys(spec.get("environment"))
                detection.services.append(
                    Service(
                        name=str(name),
                        kind=kind,
                        origin=ServiceOrigin.COMPOSE,
                        image=image
                        or (str(build) if isinstance(build, str) else ("build" if build else None)),
                        ports=ports,
                        environment_keys=environment_keys,
                        evidence=[fact(path, f"services.{name}", weight=0.85)],
                        certainty=Certainty.FACT,
                        confidence=0.9,
                    )
                )
            detection.commands.append(
                build_command(
                    "docker compose up",
                    source=CommandSource.COMPOSE,
                    path=path,
                    locator="services",
                    purpose=CommandPurpose.RUN,
                    certainty=Certainty.INFERENCE,
                    confidence=0.8,
                    detail=f"{len(services)} compose service(s) detected",
                    notes=["starts all services in the foreground"],
                )
            )
            detection.commands.append(
                build_command(
                    "docker compose config",
                    source=CommandSource.COMPOSE,
                    path=path,
                    purpose=CommandPurpose.VERIFY,
                    certainty=Certainty.INFERENCE,
                    confidence=0.75,
                    detail="validate the compose file without starting containers",
                )
            )
            for name in sorted(services):
                spec = services[name] if isinstance(services[name], dict) else {}
                kind = _service_kind(name, str(spec.get("image") or "") or None)
                if kind in (ServiceKind.DATABASE, ServiceKind.CACHE, ServiceKind.QUEUE):
                    detection.commands.append(
                        build_command(
                            f"docker compose up {name}",
                            source=CommandSource.COMPOSE,
                            path=path,
                            locator=f"services.{name}",
                            purpose=CommandPurpose.RUN,
                            certainty=Certainty.INFERENCE,
                            confidence=0.7,
                            detail=f"start the '{name}' dependency service",
                        )
                    )
            break  # the first compose file wins; override files are merged by compose itself

    # --------------------------------------------------------------- dockerfile
    def _detect_dockerfiles(self, context: DetectionContext, detection: Detection) -> None:
        dockerfiles = [
            record
            for record in context.scan.files
            if record.name.lower().startswith("dockerfile")
            and record.category.value == "infrastructure"
        ]
        for record in sorted(dockerfiles, key=lambda item: item.path):
            text = context.read(record.path)
            if not text:
                continue
            for match in _FROM_RE.finditer(text):
                detection.technologies.append(
                    DetectedTechnology(
                        kind=TechnologyKind.CONTAINER,
                        name="Docker",
                        version=None,
                        certainty=Certainty.FACT,
                        confidence=0.85,
                        evidence=[fact(record.path, "FROM", weight=0.8)],
                        notes=[f"base image: {match.group('image')}"],
                    )
                )
                break
            expose = _EXPOSE_RE.search(text)
            if expose:
                detection.notes.append(f"{record.path} exposes: {expose.group('ports').strip()}")
            env_block = _ENV_RE.findall(text)
            for entry in env_block:
                detection.env_keys.extend(_keys_from_docker_env(entry))
            cmd = _CMD_RE.search(text)
            if cmd:
                command = _clean_docker_command(cmd.group("body"))
                if command:
                    directive = "CMD" if cmd.group(0).upper().startswith("CMD") else "ENTRYPOINT"
                    detection.notes.append(
                        f"{record.path}: container {directive} is '{command}' (runs inside the image)"
                    )
            if not detection.commands or all(
                command.source is not CommandSource.DOCKERFILE for command in detection.commands
            ):
                project = slugify(str(context.root.name))
                detection.commands.append(
                    build_command(
                        f"docker build -t {project} .",
                        source=CommandSource.DOCKERFILE,
                        path=record.path,
                        purpose=CommandPurpose.BUILD,
                        certainty=Certainty.INFERENCE,
                        confidence=0.7,
                        detail="Dockerfile detected; tag derived from the repository name",
                        notes=["confirm the image tag and build context for your workflow"],
                    )
                )

    # ------------------------------------------------------------------ makefile
    def _detect_makefiles(self, context: DetectionContext, detection: Detection) -> None:
        records = [
            record
            for record in context.scan.files
            if record.name.lower() in {"makefile", "gnumakefile"}
        ]
        for record in sorted(records, key=lambda item: item.path)[:3]:
            text = context.read(record.path)
            if not text:
                continue
            commands = makefile_commands(record.path, text, cwd=record.parent)
            if not commands:
                continue
            detection.technologies.append(
                DetectedTechnology(
                    kind=TechnologyKind.BUILD_TOOL,
                    name="Make",
                    certainty=Certainty.FACT,
                    confidence=0.85,
                    evidence=[fact(record.path, weight=0.85)],
                    notes=[f"{len(commands)} runnable target(s)"],
                )
            )
            detection.commands.extend(commands)

    def _detect_taskfiles(self, context: DetectionContext, detection: Detection) -> None:
        for name in ("Taskfile.yml", "Taskfile.yaml", "taskfile.yml", "taskfile.yaml"):
            if not context.exists(name):
                continue
            data = context.yaml(name)
            commands = taskfile_commands(name, data)
            if not commands:
                continue
            detection.technologies.append(
                DetectedTechnology(
                    kind=TechnologyKind.BUILD_TOOL,
                    name="Task",
                    certainty=Certainty.FACT,
                    confidence=0.85,
                    evidence=[fact(name, "tasks", weight=0.85)],
                )
            )
            detection.commands.extend(commands)

    def _detect_procfiles(self, context: DetectionContext, detection: Detection) -> None:
        if not context.exists("Procfile"):
            return
        text = context.read("Procfile")
        if not text:
            return
        detection.technologies.append(
            DetectedTechnology(
                kind=TechnologyKind.TOOL,
                name="Procfile",
                certainty=Certainty.FACT,
                confidence=0.8,
                evidence=[fact("Procfile", weight=0.8)],
            )
        )
        detection.commands.extend(procfile_commands("Procfile", text))

    # ------------------------------------------------------------- orchestration
    def _detect_orchestration(self, context: DetectionContext, detection: Detection) -> None:
        tf_records = context.records_with_suffix(".tf")
        if tf_records:
            detection.technologies.append(
                DetectedTechnology(
                    kind=TechnologyKind.CLOUD,
                    name="Terraform",
                    certainty=Certainty.FACT,
                    confidence=0.85,
                    evidence=[fact(tf_records[0].path, weight=0.8)],
                )
            )
            detection.risks.append(
                Risk(
                    id="terraform-apply",
                    title="Terraform configuration present",
                    description=(
                        "Infrastructure code can create or destroy cloud resources. "
                        "Run `terraform plan` first and review changes before applying."
                    ),
                    severity=Severity.HIGH,
                    category=RiskCategory.DATA,
                    evidence=[fact(tf_records[0].path, weight=0.8)],
                    mitigation="Use terraform plan and require human approval before apply.",
                )
            )
        k8s_dirs = context.scan.directories_matching("k8s") + context.scan.directories_matching(
            "kubernetes"
        )
        helm_charts = context.records_named("Chart.yaml")
        if k8s_dirs or helm_charts:
            evidence = (
                [fact(helm_charts[0].path, weight=0.8)]
                if helm_charts
                else [fact(k8s_dirs[0], weight=0.7)]
            )
            detection.technologies.append(
                DetectedTechnology(
                    kind=TechnologyKind.ORCHESTRATION,
                    name="Kubernetes" if not helm_charts else "Helm",
                    certainty=Certainty.FACT,
                    confidence=0.8,
                    evidence=evidence,
                )
            )
            detection.risks.append(
                Risk(
                    id="kubernetes-apply",
                    title="Kubernetes manifests present",
                    description="Applying manifests can change live cluster state.",
                    severity=Severity.MEDIUM,
                    category=RiskCategory.RELIABILITY,
                    evidence=evidence,
                    mitigation="Use `kubectl diff` / `helm template` before applying.",
                )
            )
        nginx = context.records_named("nginx.conf")
        if nginx:
            detection.technologies.append(
                DetectedTechnology(
                    kind=TechnologyKind.WEB_SERVER,
                    name="nginx",
                    certainty=Certainty.FACT,
                    confidence=0.8,
                    evidence=[fact(nginx[0].path, weight=0.8)],
                )
            )


def _service_kind(name: str, image: str | None) -> ServiceKind:
    lowered = f"{name} {image or ''}".lower()
    if any(token in lowered for token, _engine in _DB_IMAGE_HINTS):
        return ServiceKind.DATABASE
    if any(hint in lowered for hint in _CACHE_IMAGE_HINTS):
        return ServiceKind.CACHE
    if any(hint in lowered for hint in _QUEUE_IMAGE_HINTS):
        return ServiceKind.QUEUE
    if any(hint in lowered for hint in _STORAGE_IMAGE_HINTS):
        return ServiceKind.STORAGE
    if any(hint in lowered for hint in ("nginx", "traefik", "caddy", "envoy")):
        return ServiceKind.PROXY
    if any(hint in lowered for hint in ("web", "app", "frontend", "api", "backend", "server")):
        return ServiceKind.WEB
    if any(
        hint in lowered
        for hint in ("worker", "celery", "queue-consumer", "consumer", "scheduler", "cron")
    ):
        return ServiceKind.WORKER
    return ServiceKind.OTHER


def _environment_keys(environment: Any) -> list[str]:
    """Extract environment variable *names*; values are never retained."""
    keys: list[str] = []
    if isinstance(environment, dict):
        keys.extend(str(key) for key in environment)
    elif isinstance(environment, list):
        for entry in environment:
            text = str(entry)
            name, _, _value = text.partition("=")
            if name.strip():
                keys.append(name.strip())
    return sorted(set(keys))


def _keys_from_docker_env(body: str) -> list[str]:
    keys: list[str] = []
    for token in body.split():
        if "=" in token:
            keys.append(token.split("=", 1)[0])
        elif token.isupper() and token.replace("_", "").isalnum():
            keys.append(token)
    return keys


def _clean_docker_command(body: str) -> str:
    text = body.strip()
    if text.startswith("[") and text.endswith("]"):
        parts = [part.strip().strip('"').strip("'") for part in text[1:-1].split(",")]
        return " ".join(part for part in parts if part)
    return text
