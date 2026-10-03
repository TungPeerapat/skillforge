"""Command parsing and purpose classification.

This module is the single place where repository text becomes a
:class:`~skillforge.models.workflow.Command`. Every command produced here carries
an :class:`~skillforge.models.common.Evidence` entry and a risk classification;
nothing is ever executed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from skillforge.models import Certainty, Command, Evidence
from skillforge.models.workflow import CommandPurpose, CommandSource
from skillforge.security.command_risk import classify_command
from skillforge.utils.markdown import fenced_code_blocks
from skillforge.utils.text import strip_command_prefix, truncate

PACKAGE_MANAGERS: Final[frozenset[str]] = frozenset({"npm", "pnpm", "yarn", "bun"})

#: First tokens accepted from documentation code blocks. Anything else is
#: treated as prose and ignored — this is the main defence against turning a
#: README sentence into a "discovered command".
DOC_COMMAND_RUNNERS: Final[frozenset[str]] = frozenset(
    {
        "npm",
        "pnpm",
        "yarn",
        "bun",
        "npx",
        "node",
        "pip",
        "pip3",
        "uv",
        "poetry",
        "pdm",
        "conda",
        "python",
        "python3",
        "py",
        "pytest",
        "tox",
        "nox",
        "ruff",
        "mypy",
        "black",
        "flask",
        "uvicorn",
        "gunicorn",
        "django-admin",
        "go",
        "dotnet",
        "flutter",
        "dart",
        "docker",
        "docker-compose",
        "make",
        "task",
        "just",
        "alembic",
        "prisma",
        "flyway",
        "mvn",
        "./mvnw",
        "gradle",
        "gradlew",
        "./gradlew",
        "cargo",
        "composer",
        "bundle",
        "rails",
        "artisan",
        "php",
        "mix",
        "ctest",
        "pio",
        "idf.py",
    }
)

_DOC_BLOCK_LANGUAGES: Final[frozenset[str]] = frozenset(
    {"bash", "sh", "shell", "console", "terminal", "zsh", "powershell", "ps1", "cmd", "bat"}
)

#: Documentation blocks that are about installing SkillForge or other tools
#: unrelated to the project being analysed.
_DOC_NOISE_PREFIXES: Final[tuple[str, ...]] = (
    "git clone",
    "git checkout",
    "cd ",
    "export ",
    "set ",
    "source ",
    "conda activate",
    "pyenv ",
    "nvm ",
)

_PURPOSE_PATTERNS: Final[tuple[tuple[CommandPurpose, tuple[str, ...]], ...]] = (
    (
        CommandPurpose.MIGRATE,
        (r"migrat", r"alembic", r"prisma", r"flyway", r"liquibase", r"\bupgrade\b", r"schema"),
    ),
    (CommandPurpose.SEED, (r"seed", r"fixture", r"demo[_-]?data")),
    (
        CommandPurpose.TEST,
        (r"(^|[-_:])tests?($|[-_:])", r"^test", r"e2e", r"integ", r"unit", r"spec", r"coverage"),
    ),
    (
        CommandPurpose.TYPECHECK,
        (r"typecheck", r"type[_-]?check", r"\btypes\b", r"\btsc\b", r"mypy", r"pyright"),
    ),
    (CommandPurpose.LINT, (r"lint", r"eslint", r"flake8", r"pylint", r"golangci")),
    (CommandPurpose.FORMAT, (r"format", r"prettier", r"\bblack\b", r"gofmt")),
    (CommandPurpose.BUILD, (r"\bbuild\b", r"compile", r"bundle", r"\bdist\b", r"\bpackage\b")),
    (CommandPurpose.DEPLOY, (r"\bdeploy\b", r"\bship\b", r"release", r"publish", r"\bprod\b")),
    (CommandPurpose.CLEAN, (r"clean", r"purge", r"prune")),
    (CommandPurpose.DOCS, (r"\bdocs?\b", r"documentation")),
    (
        CommandPurpose.SETUP,
        (r"install", r"setup", r"bootstrap", r"prepare", r"postinstall", r"\bdeps\b", r"sync"),
    ),
    (CommandPurpose.RUN, (r"^(dev|start|serve|run|watch)", r"dev:", r"serve:", r"^app$")),
    (CommandPurpose.VERIFY, (r"verify", r"\bcheck\b", r"health", r"smoke", r"validate")),
)

_BODY_HINTS: Final[tuple[tuple[CommandPurpose, tuple[str, ...]], ...]] = (
    (
        CommandPurpose.MIGRATE,
        (
            r"\balembic\b",
            r"\bflyway\b",
            r"\bliquibase\b",
            r"prisma\s+migrate",
            r"manage\.py\s+migrate",
            r"dotnet\s+ef\b",
            r"knex\s+db:migrate",
            r"sequelize\s+db:migrate",
            r"\bgoose\s+up\b",
            r"\bdbmate\s+up\b",
            r"\batlas\s+migrate\b",
        ),
    ),
    (CommandPurpose.SEED, (r"\bseed\b", r"loaddata", r"db:seed")),
    (
        CommandPurpose.TEST,
        (
            r"\bpytest\b",
            r"\bjest\b",
            r"\bvitest\b",
            r"\bmocha\b",
            r"\bplaywright\b",
            r"\bcypress\b",
            r"go\s+test",
            r"dotnet\s+test",
            r"flutter\s+test",
            r"cargo\s+test",
            r"dart\s+test",
            r"rspec",
            r"phpunit",
            r"ctest",
            r"\b(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?test\b",
            r"\b(?:npm|pnpm|yarn|bun)\s+run\s+tests\b",
        ),
    ),
    (
        CommandPurpose.LINT,
        (
            r"\bruff\b",
            r"\beslint\b",
            r"\bgolangci-lint\b",
            r"\bflake8\b",
            r"\bpylint\b",
            r"flutter\s+analyze",
        ),
    ),
    (CommandPurpose.TYPECHECK, (r"\bmypy\b", r"\btsc\b", r"\bpyright\b")),
    (
        CommandPurpose.FORMAT,
        (r"\bprettier\b", r"\bblack\b", r"\bgofmt\b", r"dart\s+format", r"dotnet\s+format"),
    ),
    (
        CommandPurpose.BUILD,
        (
            r"go\s+build",
            r"dotnet\s+build",
            r"flutter\s+build",
            r"\bnext\s+build\b",
            r"cargo\s+build",
            r"webpack",
            r"\bvite\s+build\b",
            r"\bturbo\s+build\b",
            r"mvn\s+package",
            r"gradlew?\s+build",
        ),
    ),
    (
        CommandPurpose.DEPLOY,
        (
            r"\bdeploy\b",
            r"\bpublish\b",
            r"docker\s+push",
            r"kubectl\s+apply",
            r"helm\s+upgrade",
            r"terraform\s+apply",
            r"gh\s+release\s+create",
        ),
    ),
    (
        CommandPurpose.SETUP,
        (
            r"\bpip\s+install\b",
            r"\buv\s+(?:sync|pip\s+install)\b",
            r"\bpoetry\s+install\b",
            r"\bpdm\s+install\b",
            r"\bnpm\s+(?:install|ci)\b",
            r"\bpnpm\s+(?:install|i)\b",
            r"\byarn\s+install\b",
            r"\bbun\s+install\b",
            r"\bbundle\s+install\b",
            r"\bcomposer\s+install\b",
            r"go\s+mod\s+download",
            r"dotnet\s+restore",
            r"flutter\s+pub\s+get",
            r"dart\s+pub\s+get",
        ),
    ),
    (
        CommandPurpose.RUN,
        (
            r"uvicorn",
            r"gunicorn",
            r"\bflask\s+run\b",
            r"next\s+dev",
            r"\bvite\b",
            r"manage\.py\s+runserver",
            r"\bnodemon\b",
            r"flutter\s+run",
            r"\bair\b",
            r"docker[\s-]compose\s+up",
            r"docker\s+run\b",
            r"dotnet\s+run\b",
            r"go\s+run\b",
            r"mvn\s+spring-boot:run",
            r"gradlew?\s+bootRun",
            r"uv\s+run\s+(?:uvicorn|gunicorn|flask)",
        ),
    ),
    (
        CommandPurpose.VERIFY,
        (
            r"docker[\s-]compose\s+config",
            r"kubectl\s+diff",
            r"helm\s+template",
            r"health",
            r"smoke",
            r"\bvalidate\b",
        ),
    ),
)


def classify_purpose(name: str, body: str = "") -> CommandPurpose:
    """Classify a script/target/step by its name, then by its body."""
    lowered = name.strip().lower()
    for purpose, patterns in _PURPOSE_PATTERNS:
        if any(re.search(pattern, lowered) for pattern in patterns):
            return purpose
    if body:
        lowered_body = body.lower()
        for purpose, patterns in _BODY_HINTS:
            if any(re.search(pattern, lowered_body) for pattern in patterns):
                return purpose
    return CommandPurpose.OTHER


def script_command(package_manager: str, script_name: str) -> str:
    """Render the canonical command for a package.json script."""
    manager = package_manager if package_manager in PACKAGE_MANAGERS else "npm"
    return f"{manager} run {script_name}"


def build_command(
    command: str,
    *,
    source: CommandSource,
    path: str,
    locator: str = "",
    purpose: CommandPurpose = CommandPurpose.OTHER,
    cwd: str = ".",
    certainty: Certainty = Certainty.FACT,
    confidence: float = 0.8,
    detail: str = "",
    snippet: str = "",
    script_body: str | None = None,
    component: str | None = None,
    placeholders: list[str] | None = None,
    notes: list[str] | None = None,
) -> Command:
    """Create a :class:`Command` with evidence and risk classification."""
    cleaned = command.strip()
    evidence = Evidence(
        kind=certainty,
        source=path,
        locator=locator,
        detail=detail,
        snippet=snippet,
        weight=confidence,
    )
    risk = classify_command(cleaned, script_body=script_body)
    return Command(
        command=cleaned,
        source=source,
        purpose=purpose,
        cwd=cwd,
        component=component,
        evidence=[evidence],
        confidence=confidence if certainty is Certainty.FACT else min(confidence, 0.7),
        certainty=certainty,
        risk=risk.level,
        risk_reasons=list(risk.reasons),
        placeholders=placeholders if placeholders is not None else extract_placeholders(cleaned),
        notes=notes or [],
        script_body=truncate(script_body, 600) if script_body else None,
    )


_PLACEHOLDER_RE = re.compile(
    r"\$\{\{[^}]+\}\}|\$\{[^}]+\}|\{[A-Za-z_][A-Za-z0-9_]*\}|<[A-Za-z][A-Za-z0-9_.:-]*>"
)


def extract_placeholders(command: str) -> list[str]:
    return sorted(set(_PLACEHOLDER_RE.findall(command)))


# --------------------------------------------------------------------- Makefile


@dataclass(frozen=True)
class MakeTarget:
    name: str
    recipe: str
    line: int
    phony: bool = False


_TARGET_RE = re.compile(r"^(?P<name>[A-Za-z][A-Za-z0-9_-]*)\s*:(?!=)(?P<deps>.*)$")


def parse_makefile(text: str) -> list[MakeTarget]:
    """Parse simple ``target: deps`` rules with tab-indented recipes."""
    targets: list[MakeTarget] = []
    phony: set[str] = set()
    lines = text.splitlines()
    index = 0
    current: MakeTarget | None = None
    recipe: list[str] = []
    while index < len(lines):
        line = lines[index]
        if "\t" in line[:1] or (line.startswith("    ") and current is not None):
            if current is not None:
                recipe.append(line.strip())
            index += 1
            continue
        if current is not None:
            targets.append(
                MakeTarget(
                    name=current.name,
                    recipe="\n".join(recipe).strip(),
                    line=current.line,
                    phony=current.name in phony,
                )
            )
            current, recipe = None, []
        stripped = line.strip()
        if stripped.startswith(".PHONY"):
            _, _, names = stripped.partition(":")
            phony.update(names.split())
            index += 1
            continue
        if (
            stripped.startswith(("#", ".", "include", "export", "ifeq", "endif", "else"))
            or "=" in stripped.split(":", 1)[0]
        ):
            index += 1
            continue
        match = _TARGET_RE.match(stripped)
        if match:
            current = MakeTarget(name=match.group("name"), recipe="", line=index + 1)
        index += 1
    if current is not None:
        targets.append(
            MakeTarget(
                name=current.name,
                recipe="\n".join(recipe).strip(),
                line=current.line,
                phony=current.name in phony,
            )
        )
    return [target for target in targets if target.recipe]


def makefile_commands(rel_path: str, text: str, *, cwd: str = ".") -> list[Command]:
    """Turn Makefile targets into commands."""
    commands: list[Command] = []
    for target in parse_makefile(text):
        purpose = classify_purpose(target.name, target.recipe)
        commands.append(
            build_command(
                f"make {target.name}",
                source=CommandSource.MAKEFILE,
                path=rel_path,
                locator=f"L{target.line}",
                purpose=purpose,
                cwd=cwd,
                detail=f"Makefile target '{target.name}'",
                snippet=target.recipe,
                script_body=target.recipe,
                certainty=Certainty.FACT,
                confidence=0.85,
            )
        )
    return commands


# --------------------------------------------------------------------- Taskfile


def taskfile_commands(rel_path: str, data: object, *, cwd: str = ".") -> list[Command]:
    """Turn Taskfile tasks into commands."""
    if not isinstance(data, dict):
        return []
    tasks = data.get("tasks")
    if not isinstance(tasks, dict):
        return []
    commands: list[Command] = []
    for name, definition in tasks.items():
        body = ""
        if isinstance(definition, dict):
            entries = definition.get("cmds") or definition.get("commands") or []
            body = (
                "\n".join(str(entry) for entry in entries)
                if isinstance(entries, list)
                else str(entries)
            )
        elif isinstance(definition, str):
            body = definition
        purpose = classify_purpose(str(name), body)
        commands.append(
            build_command(
                f"task {name}",
                source=CommandSource.TASKFILE,
                path=rel_path,
                locator=f"tasks.{name}",
                purpose=purpose,
                cwd=cwd,
                detail=f"Taskfile task '{name}'",
                snippet=body,
                script_body=body,
                confidence=0.85,
            )
        )
    return commands


# -------------------------------------------------------------------- Procfile


def procfile_commands(rel_path: str, text: str, *, cwd: str = ".") -> list[Command]:
    """Parse ``web: uvicorn app:app`` style Procfile entries."""
    commands: list[Command] = []
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        name, _, body = stripped.partition(":")
        body = body.strip()
        if not body:
            continue
        commands.append(
            build_command(
                body,
                source=CommandSource.PROCFILE,
                path=rel_path,
                locator=f"L{number}",
                purpose=CommandPurpose.RUN,
                cwd=cwd,
                detail=f"Procfile process '{name.strip()}'",
                confidence=0.8,
            )
        )
    return commands


# ------------------------------------------------------- documentation parsing


def commands_from_markdown(
    rel_path: str,
    text: str,
    *,
    source: CommandSource = CommandSource.README,
    cwd: str = ".",
    max_commands: int = 40,
) -> list[Command]:
    """Extract commands from fenced code blocks in documentation.

    Only lines whose first token is a known project tooling command are kept,
    which makes README parsing hostile-input resistant: prose, URLs and
    installation instructions for unrelated tools are ignored.
    """
    commands: list[Command] = []
    seen: set[str] = set()
    for block in fenced_code_blocks(text):
        if block.lang and block.lang not in _DOC_BLOCK_LANGUAGES:
            continue
        for offset, raw_line in enumerate(block.content.splitlines()):
            line = strip_command_prefix(raw_line)
            if not line or line.startswith("#"):
                continue
            if len(line) > 200:
                continue
            token = line.split()[0] if line.split() else ""
            if token not in DOC_COMMAND_RUNNERS:
                continue
            if any(line.startswith(prefix) for prefix in _DOC_NOISE_PREFIXES):
                continue
            if token in {"python", "pip", "uv", "poetry"} and "install" in line and "-e ." in line:
                purpose = CommandPurpose.SETUP
            else:
                purpose = classify_purpose(line, line)
            if line in seen:
                continue
            seen.add(line)
            commands.append(
                build_command(
                    line,
                    source=source,
                    path=rel_path,
                    locator=f"L{block.line + offset}",
                    purpose=purpose,
                    cwd=cwd,
                    detail=f"documented command in {rel_path}",
                    certainty=Certainty.FACT,
                    confidence=0.6,
                    notes=["documented in prose; verify before running"],
                )
            )
            if len(commands) >= max_commands:
                return commands
    return commands


def unique_commands(commands: list[Command]) -> list[Command]:
    """De-duplicate commands by (command, cwd), keeping the first occurrence."""
    seen: set[tuple[str, str]] = set()
    result: list[Command] = []
    for command in commands:
        key = (command.command, command.cwd)
        if key in seen:
            continue
        seen.add(key)
        result.append(command)
    return result


def dedupe_evidence(evidence: list[Evidence]) -> list[Evidence]:
    seen: set[str] = set()
    result: list[Evidence] = []
    for item in evidence:
        if item.label in seen:
            continue
        seen.add(item.label)
        result.append(item)
    return result
