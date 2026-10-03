"""CI/CD detector: GitHub Actions, GitLab CI, and common alternatives."""

from __future__ import annotations

from typing import Any, Final

from skillforge.analyzer.detectors.base import Detection, DetectionContext, fact
from skillforge.discovery.commands import build_command, classify_purpose
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

_GH_WORKFLOW_PREFIX = ".github/workflows/"
_MAX_COMMANDS_PER_WORKFLOW = 20

_OTHER_CI_FILES: Final[dict[str, str]] = {
    ".gitlab-ci.yml": "GitLab CI",
    "Jenkinsfile": "Jenkins",
    "azure-pipelines.yml": "Azure Pipelines",
    "bitbucket-pipelines.yml": "Bitbucket Pipelines",
    ".travis.yml": "Travis CI",
    ".circleci/config.yml": "CircleCI",
    "buildkite.yml": "Buildkite",
    ".woodpecker.yml": "Woodpecker",
}

_SETUP_ACTIONS: Final[dict[str, str]] = {
    "actions/setup-python": "Python",
    "actions/setup-node": "Node.js",
    "actions/setup-go": "Go",
    "actions/setup-dotnet": ".NET",
    "actions/setup-java": "Java",
    "docker/setup-buildx-action": "Docker",
    "docker/build-push-action": "Docker",
    "subosito/flutter-action": "Flutter",
    "golangci/golangci-lint-action": "golangci-lint",
    "aws-actions/configure-aws-credentials": "AWS",
    "google-github-actions/auth": "Google Cloud",
    "azure/login": "Azure",
}

#: Toolchains detected from manifests; CI references are notes, not technologies.
_LANGUAGE_TOOLCHAIN_NAMES: Final[frozenset[str]] = frozenset(
    {"Python", "Node.js", "Go", ".NET", "Java", "Flutter", "Docker"}
)


class CIDetector:
    """Detects CI pipelines and extracts their commands as evidence."""

    id = "ci"

    def applies(self, context: DetectionContext) -> bool:
        if any(record.path.startswith(_GH_WORKFLOW_PREFIX) for record in context.scan.files):
            return True
        if context.records_named(*_OTHER_CI_FILES.keys()):
            return True
        return context.exists(".circleci/config.yml")

    def detect(self, context: DetectionContext) -> Detection:
        detection = Detection()
        workflows = [
            record for record in context.scan.files if record.path.startswith(_GH_WORKFLOW_PREFIX)
        ]
        for record in sorted(workflows, key=lambda item: item.path):
            self._detect_github_workflow(context, record.path, detection)
        if workflows:
            detection.technologies.append(
                DetectedTechnology(
                    kind=TechnologyKind.CI,
                    name="GitHub Actions",
                    certainty=Certainty.FACT,
                    confidence=0.95,
                    evidence=[fact(workflows[0].path, weight=0.9)],
                    notes=[f"{len(workflows)} workflow file(s)"],
                )
            )
        for path, name in _OTHER_CI_FILES.items():
            if not context.exists(path):
                continue
            detection.technologies.append(
                DetectedTechnology(
                    kind=TechnologyKind.CI,
                    name=name,
                    certainty=Certainty.FACT,
                    confidence=0.9,
                    evidence=[fact(path, weight=0.9)],
                )
            )
            if name == "GitLab CI":
                self._detect_gitlab(context, path, detection)
        return detection

    # ------------------------------------------------------------ github actions
    def _detect_github_workflow(
        self, context: DetectionContext, path: str, detection: Detection
    ) -> None:
        data = context.yaml(path)
        if not isinstance(data, dict):
            return
        workflow_name = str(data.get("name") or path.rsplit("/", 1)[-1])
        jobs = data.get("jobs")
        if not isinstance(jobs, dict):
            return
        emitted = 0
        for job_name in sorted(jobs):
            job = jobs[job_name]
            if not isinstance(job, dict):
                continue
            self._detect_gha_services(context, path, str(job_name), job, detection)
            steps = job.get("steps")
            if not isinstance(steps, list):
                continue
            for index, step in enumerate(steps):
                if not isinstance(step, dict):
                    continue
                step_name = str(step.get("name") or f"step {index + 1}")
                uses = step.get("uses")
                if isinstance(uses, str):
                    self._note_action(uses, path, detection)
                run = step.get("run")
                if not isinstance(run, str):
                    continue
                for line in _iter_script_lines(run):
                    if emitted >= _MAX_COMMANDS_PER_WORKFLOW:
                        detection.notes.append(
                            f"{path}: command extraction truncated at {_MAX_COMMANDS_PER_WORKFLOW} per workflow"
                        )
                        return
                    purpose = classify_purpose(step_name, line)
                    if purpose is CommandPurpose.OTHER:
                        purpose = classify_purpose(str(job_name), line)
                    if purpose is CommandPurpose.OTHER and _looks_like_deploy(job_name):
                        purpose = CommandPurpose.DEPLOY
                    detection.commands.append(
                        build_command(
                            line,
                            source=CommandSource.CI,
                            path=path,
                            locator=f"jobs.{job_name}.steps[{index}]",
                            purpose=purpose,
                            certainty=Certainty.FACT,
                            confidence=0.75,
                            detail=f"CI step '{step_name}' in job '{job_name}' ({workflow_name})",
                            notes=["runs in CI, not locally; verify before reproducing locally"],
                        )
                    )
                    emitted += 1

    def _detect_gha_services(
        self,
        context: DetectionContext,
        path: str,
        job_name: str,
        job: dict[str, Any],
        detection: Detection,
    ) -> None:
        services = job.get("services")
        if not isinstance(services, dict):
            return
        for name in sorted(services):
            spec = services[name] if isinstance(services[name], dict) else {}
            image = str(spec.get("image") or "") or None
            ports = [str(port) for port in (spec.get("ports") or []) if port is not None]
            detection.services.append(
                Service(
                    name=str(name),
                    kind=_service_kind(str(name), image),
                    origin=ServiceOrigin.CI,
                    image=image,
                    ports=ports,
                    environment_keys=sorted(
                        str(key)
                        for key in (spec.get("env") or {})
                        if isinstance(spec.get("env"), dict)
                    ),
                    evidence=[fact(path, f"jobs.{job_name}.services.{name}", weight=0.85)],
                    certainty=Certainty.FACT,
                    confidence=0.85,
                )
            )

    def _note_action(self, uses: str, path: str, detection: Detection) -> None:
        action = uses.split("@", 1)[0]
        display = _SETUP_ACTIONS.get(action)
        if not display:
            return
        if display in _LANGUAGE_TOOLCHAIN_NAMES:
            # Languages are already detected from manifests; a CI setup action
            # only confirms that the toolchain is used in CI.
            detection.notes.append(f"CI configures {display} ({action})")
            return
        detection.technologies.append(
            DetectedTechnology(
                kind=TechnologyKind.TOOL,
                name=display,
                certainty=Certainty.FACT,
                confidence=0.7,
                evidence=[fact(path, f"uses {action}", weight=0.7)],
                notes=["used in CI"],
            )
        )
        if (
            action.startswith("aws-actions")
            or action.startswith("google-github-actions")
            or action == "azure/login"
        ):
            detection.risks.append(
                Risk(
                    id=f"cloud-credentials-{slugify(display)}",
                    title=f"CI deploys to {display}",
                    description=(
                        "The pipeline authenticates to a cloud provider. Deployment jobs may mutate "
                        "production infrastructure."
                    ),
                    severity=Severity.MEDIUM,
                    category=RiskCategory.SECURITY,
                    evidence=[fact(path, f"uses {action}", weight=0.7)],
                    mitigation="Review the workflow's environment protection and approval rules.",
                )
            )

    # -------------------------------------------------------------------- gitlab
    def _detect_gitlab(self, context: DetectionContext, path: str, detection: Detection) -> None:
        data = context.yaml(path)
        if not isinstance(data, dict):
            return
        reserved = {
            "stages",
            "variables",
            "include",
            "default",
            "workflow",
            "image",
            "services",
            "before_script",
            "after_script",
            "cache",
            "pages",
        }
        emitted = 0
        for job_name, job in data.items():
            if job_name in reserved or not isinstance(job, dict):
                continue
            script = job.get("script") or job.get("before_script")
            if not isinstance(script, list):
                continue
            for index, line in enumerate(script):
                text = str(line).strip()
                if not text or emitted >= _MAX_COMMANDS_PER_WORKFLOW:
                    continue
                purpose = classify_purpose(job_name, text)
                if purpose is CommandPurpose.OTHER and _looks_like_deploy(str(job_name)):
                    purpose = CommandPurpose.DEPLOY
                detection.commands.append(
                    build_command(
                        text,
                        source=CommandSource.CI,
                        path=path,
                        locator=f"{job_name}.script[{index}]",
                        purpose=purpose,
                        certainty=Certainty.FACT,
                        confidence=0.7,
                        detail=f"GitLab CI job '{job_name}'",
                        notes=["runs in CI, not locally; verify before reproducing locally"],
                    )
                )
                emitted += 1


def _iter_script_lines(script: str) -> list[str]:
    """Split a CI ``run`` block into individual command lines."""
    lines: list[str] = []
    for raw in script.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("- "):
            continue
        if line.endswith("\\"):
            line = line[:-1].strip()
        lines.append(line)
    return lines


def _looks_like_deploy(name: str) -> bool:
    lowered = name.lower()
    return any(token in lowered for token in ("deploy", "release", "publish", "ship"))


def _service_kind(name: str, image: str | None) -> ServiceKind:
    lowered = f"{name} {image or ''}".lower()
    if any(token in lowered for token in ("postgres", "mysql", "mariadb", "mongo")):
        return ServiceKind.DATABASE
    if any(token in lowered for token in ("redis", "memcached")):
        return ServiceKind.CACHE
    if any(token in lowered for token in ("rabbitmq", "kafka", "nats")):
        return ServiceKind.QUEUE
    return ServiceKind.OTHER
