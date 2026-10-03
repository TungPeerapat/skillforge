"""Deterministic helper scripts embedded in generated skills.

Scripts are generated from packaged templates with repository-specific data
substituted as Python/JSON literals. They are never executed by SkillForge; the
validator parses them with :mod:`ast` and the security checks reject any
dangerous command that might have slipped in.
"""

from __future__ import annotations

import json
import re
import shlex

from skillforge.generator.blueprint import CommandView, SkillBlueprint
from skillforge.generator.templates import load_text_template
from skillforge.models import SkillFile

#: Command first token -> (tool name to probe, is_required).
_TOOL_ALIASES: dict[str, str] = {
    "python": "python",
    "python3": "python",
    "py": "python",
    "pip": "python",
    "pip3": "python",
    "uv": "uv",
    "poetry": "python",
    "pdm": "uv",
    "node": "node",
    "npm": "npm",
    "pnpm": "pnpm",
    "yarn": "yarn",
    "bun": "bun",
    "npx": "node",
    "go": "go",
    "dotnet": "dotnet",
    "flutter": "flutter",
    "dart": "dart",
    "docker": "docker",
    "docker-compose": "docker",
    "make": "make",
    "task": "task",
    "just": "just",
    "alembic": "python",
    "prisma": "node",
    "mvn": "mvn",
    "gradle": "gradle",
    "./gradlew": "gradle",
    "./mvnw": "mvn",
    "mvnw": "mvn",
    "gradlew": "gradle",
    "cargo": "cargo",
    "php": "php",
    "composer": "composer",
    "bundle": "bundle",
    "mix": "mix",
    "pytest": "python",
    "ruff": "python",
    "mypy": "python",
    "black": "python",
    "flask": "python",
    "uvicorn": "python",
    "gunicorn": "python",
    "django-admin": "python",
    "jest": "node",
    "vitest": "node",
    "tsc": "node",
    "eslint": "node",
    "prettier": "node",
    "next": "node",
    "vite": "node",
}

_WRAPPER_FILES: tuple[str, ...] = ("mvnw", "gradlew")

_SHELL_TOKENS = ("&&", "||", "|", ";", ">", "<", "$(", "`")


def _tool_for(command: str) -> str | None:
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    if not parts:
        return None
    binary = parts[0]
    if binary in _TOOL_ALIASES:
        return _TOOL_ALIASES[binary]
    # package-manager script wrappers, e.g. `go test`, `dotnet ef`
    if len(parts) > 1 and parts[1] in _TOOL_ALIASES:
        return _TOOL_ALIASES[parts[1]]
    return None


def required_tools(commands: tuple[CommandView, ...]) -> list[str]:
    tools: list[str] = []
    for command in commands:
        tool = _tool_for(command.command)
        if tool and tool not in tools:
            tools.append(tool)
    return sorted(tools)


def _replace(template: str, replacements: dict[str, str]) -> str:
    result = template
    for key, value in replacements.items():
        marker = f"__SKILLFORGE_{key}__"
        if marker not in result:
            continue
        result = result.replace(marker, value)
    missing = re.findall(r"__SKILLFORGE_[A-Z_]+__", result)
    if missing:  # pragma: no cover - template/definition mismatch
        raise ValueError(f"unsubstituted template placeholders: {sorted(set(missing))}")
    return result


def _literal(value: object) -> str:
    return json.dumps(value)


def preflight_script(blueprint: SkillBlueprint, *, required_files: list[str]) -> SkillFile:
    tools = required_tools(blueprint.commands)
    optional = sorted({"docker", "make", "git"} - set(tools))
    content = _replace(
        load_text_template("scripts/preflight.py"),
        {
            "REQUIRED_TOOLS": _literal(tools),
            "OPTIONAL_TOOLS": _literal(optional),
            "REQUIRED_FILES": _literal(required_files),
        },
    )
    return SkillFile(
        path="scripts/preflight.py",
        content=content,
        executable=True,
        description="Check that required tools and files are available (read-only).",
    )


def run_steps_script(blueprint: SkillBlueprint) -> SkillFile:
    steps = []
    for index, command in enumerate(blueprint.commands, start=1):
        if command.risk_level >= 3:  # never embed dangerous commands
            continue
        steps.append(
            {
                "id": f"step-{index}",
                "command": command.command,
                "cwd": command.cwd or ".",
                "purpose": command.purpose,
                "risk": command.risk_label,
                "provenance": command.provenance,
                "shell_syntax": any(token in command.command for token in _SHELL_TOKENS),
            }
        )
    content = _replace(
        load_text_template("scripts/run_steps.py"),
        {"STEPS": json.dumps(steps, indent=4)},
    )
    return SkillFile(
        path="scripts/run_steps.py",
        content=content,
        executable=True,
        description=(
            "Run the documented commands. Safe steps run by default; review-level steps "
            "require --confirm; shell syntax is never executed automatically."
        ),
    )


def collect_context_script(blueprint: SkillBlueprint) -> SkillFile:
    tools = required_tools(blueprint.commands)
    for extra in ("git", "docker"):
        if extra not in tools and (
            extra == "git" or any(service.image for service in blueprint.services)
        ):
            tools.append(extra)
    key_files = _key_files(blueprint)
    content = _replace(
        load_text_template("scripts/collect_context.py"),
        {
            "TOOLS": _literal(sorted(tools)),
            "KEY_FILES": _literal(key_files),
            "ENVIRONMENT_KEYS": _literal(list(blueprint.environment_keys)),
        },
    )
    return SkillFile(
        path="scripts/collect_context.py",
        content=content,
        executable=True,
        description="Collect read-only debugging context (versions, git state, key files).",
    )


def migration_status_script(blueprint: SkillBlueprint) -> SkillFile:
    alembic_dirs = [
        directory
        for database in blueprint.databases
        for directory in database.migration_dirs
        if "alembic" in directory.lower()
    ]
    if not alembic_dirs and any(
        database.migration_tool and "alembic" in database.migration_tool.lower()
        for database in blueprint.databases
    ):
        alembic_dirs = ["alembic"]
    prisma_dirs = [
        directory
        for database in blueprint.databases
        for directory in database.migration_dirs
        if "prisma" in directory.lower()
    ]
    content = _replace(
        load_text_template("scripts/migration_status.py"),
        {
            "ALEMBIC_DIRS": _literal(sorted(set(alembic_dirs))),
            "PRISMA_DIRS": _literal(sorted(set(prisma_dirs))),
        },
    )
    return SkillFile(
        path="scripts/migration_status.py",
        content=content,
        executable=True,
        description="Report Alembic/Prisma migration state statically (read-only).",
    )


def check_openapi_script(blueprint: SkillBlueprint) -> SkillFile:
    base_url = _default_base_url(blueprint)
    specs = sorted({path for api in blueprint.apis for path in api.spec_paths})
    content = _replace(
        load_text_template("scripts/check_openapi.py"),
        {
            "DEFAULT_BASE_URL": _literal(base_url),
            "SPEC_PATHS": _literal(specs),
        },
    )
    return SkillFile(
        path="scripts/check_openapi.py",
        content=content,
        executable=True,
        description="List OpenAPI routes from a running service (GET only, read-only).",
    )


def release_preflight_script(blueprint: SkillBlueprint) -> SkillFile:
    version_sources = [
        ("pyproject.toml", r'^version\s*=\s*"([^"]+)"'),
        ("package.json", r'"version"\s*:\s*"([^"]+)"'),
        ("src/version.py", r"__version__\s*=\s*[\"']([^\"']+)[\"']"),
    ]
    existing = [list(item) for item in version_sources if _file_likely_exists(blueprint, item[0])]
    changelogs = [doc.path for doc in blueprint.docs if doc.kind == "changelog"] or [
        "CHANGELOG.md",
        "CHANGES.md",
    ]
    deploy = [
        command.command
        for command in blueprint.commands
        if command.purpose in {"deploy", "release"}
    ]
    content = _replace(
        load_text_template("scripts/release_preflight.py"),
        {
            "VERSION_SOURCES": _literal(existing),
            "CHANGELOG_CANDIDATES": _literal(changelogs),
            "DEPLOY_COMMANDS": _literal(deploy),
        },
    )
    return SkillFile(
        path="scripts/release_preflight.py",
        content=content,
        executable=True,
        description="Read-only release checklist (git state, changelog, version, deploy steps).",
    )


def scripts_for_skill(skill: str, blueprint: SkillBlueprint) -> list[SkillFile]:
    """Return the scripts a given skill ships with."""
    files: list[SkillFile] = []
    if skill in {"project-runner", "project-builder", "test-runner", "code-reviewer"}:
        files.append(preflight_script(blueprint, required_files=_manifest_files(blueprint)))
        files.append(run_steps_script(blueprint))
    elif skill == "project-debugger":
        files.append(collect_context_script(blueprint))
        files.append(run_steps_script(blueprint))
    elif skill == "database-debugger":
        files.append(preflight_script(blueprint, required_files=_manifest_files(blueprint)))
        files.append(run_steps_script(blueprint))
    elif skill == "migration-guardian":
        files.append(migration_status_script(blueprint))
        files.append(run_steps_script(blueprint))
    elif skill == "api-contract-checker":
        files.append(check_openapi_script(blueprint))
        files.append(preflight_script(blueprint, required_files=_manifest_files(blueprint)))
    elif skill == "release-verifier":
        files.append(release_preflight_script(blueprint))
        files.append(run_steps_script(blueprint))
    return files


# ------------------------------------------------------------------- internals


_MANIFEST_NAMES = (
    "pyproject.toml",
    "package.json",
    "go.mod",
    "pubspec.yaml",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "requirements.txt",
)


def _manifest_files(blueprint: SkillBlueprint) -> list[str]:
    """Manifest paths implied by the blueprint's components and commands."""
    candidates: list[str] = []
    for component in blueprint.components:
        prefix = "" if component.path in (".", "") else component.path.rstrip("/") + "/"
        for name in _MANIFEST_NAMES:
            path = f"{prefix}{name}"
            if _file_likely_exists(blueprint, path):
                candidates.append(path)
    if not candidates:
        candidates = ["pyproject.toml" if "python" in blueprint.languages else "package.json"]
    return sorted(dict.fromkeys(candidates))


def _file_likely_exists(blueprint: SkillBlueprint, relative: str) -> bool:
    """Heuristic: a file exists when some evidence references it or its directory."""
    for command in blueprint.commands:
        if relative in command.command:
            return True
        for evidence in command.evidence:
            if evidence.source == relative or evidence.source.startswith(
                relative.rsplit("/", 1)[0] + "/" if "/" in relative else "\0"
            ):
                return True
    for doc in blueprint.docs:
        if doc.path == relative:
            return True
    if "/" not in relative and blueprint.components:
        return any(component.path in (".", "") for component in blueprint.components)
    prefix = relative.rsplit("/", 1)[0]
    return any(component.path == prefix for component in blueprint.components)


def _key_files(blueprint: SkillBlueprint) -> list[str]:
    """Files worth showing in the debugging context (config, manifests, entry points)."""
    candidates: list[str] = list(_manifest_files(blueprint))
    candidates.extend(doc.path for doc in blueprint.docs if doc.kind in {"readme", "architecture"})
    for database in blueprint.databases:
        candidates.extend(database.migration_dirs)
    inferred = [
        "docker-compose.yml",
        "compose.yml",
        "alembic.ini",
        "prisma/schema.prisma",
        ".env.example",
    ]
    for name in inferred:
        if _file_likely_exists(blueprint, name) and name not in candidates:
            candidates.append(name)
    seen: list[str] = []
    for item in candidates:
        if item not in seen:
            seen.append(item)
    return seen[:12]


_APPLICATION_SERVICE_KINDS = {"web", "proxy", "worker", "other"}


def _default_base_url(blueprint: SkillBlueprint) -> str:
    """Infer a local base URL from server flags, then application ports."""
    for command in blueprint.commands:
        match = re.search(r"--port[= ](\d{2,5})", command.command)
        if match:
            return f"http://localhost:{match.group(1)}"
    for service in blueprint.services:
        if service.kind not in _APPLICATION_SERVICE_KINDS:
            continue
        for mapping in service.ports:
            host_part = mapping.split(":")[-1] if ":" in mapping else mapping
            if host_part.isdigit():
                return f"http://localhost:{host_part}"
    if any(
        token in command.command
        for command in blueprint.commands
        for token in ("uvicorn", "gunicorn", "flask run")
    ):
        return "http://localhost:8000"
    if any("dotnet" in command.command for command in blueprint.commands):
        return "http://localhost:5000"
    return "http://localhost:8000"
