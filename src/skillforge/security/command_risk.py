"""Command risk classification.

Commands are classified **statically** from their token structure. Nothing is
ever executed, and the classifier never performs glob expansion or shell
evaluation. When a package script body is available, the body is classified too
so that a harmless-looking ``npm run clean`` cannot smuggle a destructive
payload past the classifier.

Levels:

``safe``
    Read-only or side-effect-free for the repository (tests, lint, builds).
``review``
    Can modify the machine, the repository, remote state, or a database.
    A human (or the agent, with user confirmation) should review it first.
``dangerous``
    Destructive or remote-code-execution patterns. SkillForge never puts these
    in a generated skill script, and the validator raises an error when one
    appears in a generated SKILL.md.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Final

from skillforge.models.common import RiskLevel
from skillforge.utils.text import normalize_whitespace

# ``RiskLevel`` is re-exported for callers that historically imported it here.
__all__ = ["CommandRisk", "RiskLevel", "classify_command", "classify_many"]


@dataclass(frozen=True)
class CommandRisk:
    """Classification result for a single command."""

    level: RiskLevel
    reasons: tuple[str, ...] = ()
    matched_rules: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return self.level.label

    @property
    def is_safe(self) -> bool:
        return self.level is RiskLevel.SAFE

    @property
    def is_dangerous(self) -> bool:
        return self.level is RiskLevel.DANGEROUS


@dataclass(frozen=True)
class _Rule:
    id: str
    pattern: re.Pattern[str]
    level: RiskLevel
    reason: str


_TEST_RUNNERS: Final[frozenset[str]] = frozenset(
    {
        "pytest",
        "jest",
        "vitest",
        "mocha",
        "jasmine",
        "ctest",
        "rspec",
        "phpunit",
        "nunit",
        "bats",
        "tox",
        "nox",
        "playwright",
        "cypress",
        "gotestsum",
        "coverage",
        "unittest",
    }
)
_LINTERS: Final[frozenset[str]] = frozenset(
    {
        "ruff",
        "black",
        "flake8",
        "pylint",
        "isort",
        "mypy",
        "pyright",
        "eslint",
        "prettier",
        "golangci-lint",
        "gofmt",
        "goimports",
        "rubocop",
        "shellcheck",
        "hadolint",
        "yamllint",
        "markdownlint",
        "stylelint",
        "clippy",
    }
)
_SAFE_GIT_SUBCOMMANDS: Final[frozenset[str]] = frozenset(
    {"status", "log", "diff", "show", "branch", "rev-parse", "describe", "ls-files", "blame"}
)
_REVIEW_GIT_SUBCOMMANDS: Final[frozenset[str]] = frozenset(
    {"push", "pull", "fetch", "merge", "rebase", "cherry-pick", "stash", "reset", "clean", "tag"}
)
_COMPOSE_READ_ONLY: Final[frozenset[str]] = frozenset({"config", "ps", "logs", "images", "version"})
_DOCKER_READ_ONLY: Final[frozenset[str]] = frozenset(
    {"ps", "images", "inspect", "version", "info", "stats", "logs", "compose/ps"}
)
_MAKE_SAFE_TARGETS: Final[frozenset[str]] = frozenset(
    {"test", "tests", "lint", "check", "format", "build", "all", "typecheck", "types"}
)

_RULES: Final[tuple[_Rule, ...]] = (
    # ---------------------------------------------------------------- dangerous
    _Rule(
        "rm-rf-root",
        re.compile(
            r"\brm\s+(?:-[A-Za-z]*[rR][A-Za-z]*f[A-Za-z]*|-[A-Za-z]*f[A-Za-z]*[rR][A-Za-z]*)\s+"
            r"(?:/|/\*|~|\$HOME|\$\{HOME\}|\*)\s*(?:$|[;&|])"
        ),
        RiskLevel.DANGEROUS,
        "recursive force-delete of a root or home directory",
    ),
    _Rule(
        "disk-write",
        re.compile(r"\b(?:dd\s+[^|;]*of=/dev/|mkfs(?:\.\w+)?\b|fdisk\b|parted\s+/dev/)"),
        RiskLevel.DANGEROUS,
        "writes to a raw disk device",
    ),
    _Rule(
        "fork-bomb",
        re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"),
        RiskLevel.DANGEROUS,
        "fork bomb",
    ),
    _Rule(
        "pipe-to-shell",
        re.compile(
            r"\b(?:curl|wget|iwr|Invoke-WebRequest)\b[^|;]*\|\s*(?:sudo\s+)?(?:ba|z|k)?sh\b",
            re.IGNORECASE,
        ),
        RiskLevel.DANGEROUS,
        "downloads a script and pipes it directly into a shell",
    ),
    _Rule(
        "power-control",
        re.compile(r"\b(?:shutdown|reboot|halt|poweroff)\b"),
        RiskLevel.DANGEROUS,
        "powers off or restarts the machine",
    ),
    _Rule(
        "chmod-root",
        re.compile(r"\bchmod\s+(?:-[A-Za-z]+\s+)*777\s+(?:/|/\*)(?:\s|$)"),
        RiskLevel.DANGEROUS,
        "world-writable permissions on the filesystem root",
    ),
    _Rule(
        "windows-wipe",
        re.compile(
            r"(?i)\b(?:format\s+[A-Za-z]:|del\s+(?:/[fFsSqQ]+\s+)*[A-Za-z]:|rd\s+/s\s+/q\s+[A-Za-z]:)"
        ),
        RiskLevel.DANGEROUS,
        "destructive Windows filesystem command",
    ),
    _Rule(
        "sql-destructive",
        re.compile(r"(?i)\b(?:drop\s+(?:database|schema|table)|truncate\s+table)\b"),
        RiskLevel.DANGEROUS,
        "destructive SQL statement",
    ),
    _Rule(
        "infra-destroy",
        re.compile(
            r"\b(?:terraform\s+destroy|kubectl\s+delete\s+(?:namespace|ns|pvc|pv)\b"
            r"|aws\s+s3\s+rb\s+[^|;]*--force|aws\s+ec2\s+terminate-instances)\b"
        ),
        RiskLevel.DANGEROUS,
        "destroys infrastructure resources",
    ),
    _Rule(
        "history-rewrite",
        re.compile(r"\bgit\s+(?:filter-branch|filter-repo)\b"),
        RiskLevel.DANGEROUS,
        "rewrites repository history",
    ),
    _Rule(
        "db-reset",
        re.compile(
            r"\b(?:prisma\s+migrate\s+reset|alembic\s+downgrade\s+base|dropdb\b"
            r"|mongorestore\s+--drop|knex\s+db:rollback\b[^|;]*--all)\b"
        ),
        RiskLevel.DANGEROUS,
        "resets or drops database state",
    ),
    # ------------------------------------------------------------------- review
    _Rule(
        "sudo",
        re.compile(r"(?:^|[;&|]\s*)sudo\b"),
        RiskLevel.REVIEW,
        "runs with elevated privileges",
    ),
    _Rule(
        "system-package-install",
        re.compile(r"(?:^|[;&|]\s*)(?:brew|apt|apt-get|apk|yum|dnf|choco|winget|scoop|snap)\b"),
        RiskLevel.REVIEW,
        "installs or modifies system packages",
    ),
    _Rule(
        "dependency-install",
        re.compile(
            r"(?:^|[;&|]\s*)(?:pip|pip3|uv|poetry|pdm|conda|npm|pnpm|yarn|bun|go|dotnet|cargo|composer|bundler)\s+"
            r"(?:install|add|sync|restore|get|update|upgrade|tidy)\b"
            r"|(?:^|[;&|]\s*)(?:flutter|dart)\s+pub\s+(?:get|upgrade|add)\b"
        ),
        RiskLevel.REVIEW,
        "resolves dependencies and may execute package lifecycle scripts",
    ),
    _Rule(
        "file-removal-tool",
        re.compile(r"(?:^|[;&|]\s*)(?:rimraf|del-cli|trash|trash-cli|shx\s+rm)\b"),
        RiskLevel.REVIEW,
        "deletes files",
    ),
    _Rule(
        "migration",
        re.compile(
            r"\b(?:alembic|flyway|liquibase|goose|dbmate|atlas)\s+(?:upgrade|migrate|downgrade|apply|redo|reset)\b"
            r"|\bprisma\s+(?:migrate|db\s+push|db\s+execute)\b"
            r"|\bmanage\.py\s+migrate\b"
            r"|\b(?:knex|sequelize|typeorm)\s+db:(?:migrate|seed|rollback)\b"
            r"|\bdotnet\s+ef\s+(?:database|migrations)\b"
        ),
        RiskLevel.REVIEW,
        "applies or reverts database migrations",
    ),
    _Rule(
        "git-mutate",
        re.compile(
            r"(?:^|[;&|]\s*)git\s+(?:push|pull|fetch|merge|rebase|cherry-pick|stash|reset|clean|tag"
            r"|checkout|switch|restore|commit|add|init|clone|remote|submodule)\b"
        ),
        RiskLevel.REVIEW,
        "mutates repository or remote state",
    ),
    _Rule(
        "network-fetch",
        re.compile(r"(?:^|[;&|]\s*)(?:curl|wget|Invoke-WebRequest|iwr)\b", re.IGNORECASE),
        RiskLevel.REVIEW,
        "performs a network request",
    ),
    _Rule(
        "recursive-delete",
        re.compile(r"(?:^|[;&|]\s*)rm\s+(?:-[A-Za-z]+\s+)*-[A-Za-z]*[rR][A-Za-z]*\b"),
        RiskLevel.REVIEW,
        "recursively deletes files",
    ),
    _Rule(
        "remove",
        re.compile(r"(?:^|[;&|]\s*)rm\b"),
        RiskLevel.REVIEW,
        "deletes files",
    ),
    _Rule(
        "write-redirect",
        re.compile(r"(?<![>])>{1,2}\s*(?!/dev/null|NUL\b|&\d)\S"),
        RiskLevel.REVIEW,
        "redirects output into a file and may overwrite it",
    ),
    _Rule(
        "chmod-chown",
        re.compile(r"(?:^|[;&|]\s*)(?:chmod|chown|chgrp|attrib|icacls|takeown)\b"),
        RiskLevel.REVIEW,
        "changes file permissions or ownership",
    ),
    _Rule(
        "service-control",
        re.compile(r"(?:^|[;&|]\s*)(?:systemctl|service|supervisorctl|launchctl)\b"),
        RiskLevel.REVIEW,
        "controls system services",
    ),
    _Rule(
        "process-control",
        re.compile(r"(?:^|[;&|]\s*)(?:kill|pkill|killall|taskkill|Stop-Process)\b"),
        RiskLevel.REVIEW,
        "terminates processes",
    ),
    _Rule(
        "background",
        re.compile(r"(?:^|\s)&\s*$|\bnohup\b|\bstart\s+/b\b"),
        RiskLevel.REVIEW,
        "starts a background process",
    ),
    _Rule(
        "publish",
        re.compile(
            r"\b(?:twine\s+upload|cargo\s+publish|gem\s+push|npm\s+publish|pnpm\s+publish"
            r"|yarn\s+publish|dotnet\s+nuget\s+push|poetry\s+publish|hatch\s+publish"
            r"|flit\s+publish|gh\s+release\s+create)\b"
        ),
        RiskLevel.REVIEW,
        "publishes a package or release",
    ),
    _Rule(
        "destructive-compose",
        re.compile(r"\bdocker[\s-]compose\s+(?:down|rm)\b[^|;]*(?:-v|--volumes)\b"),
        RiskLevel.REVIEW,
        "removes containers and their data volumes",
    ),
    # --------------------------------------------------------------------- safe
    _Rule(
        "read-only-git",
        re.compile(
            r"(?:^|[;&|]\s*)git\s+(?:status|log|diff|show|branch|rev-parse|describe|ls-files|blame)\b"
        ),
        RiskLevel.SAFE,
        "read-only git command",
    ),
    _Rule(
        "test-runner",
        re.compile(
            r"(?:^|[;&|]\s*)(?:python(?:3)?(?:\.\d+)?\s+-m\s+)?(?:pytest|unittest|tox|nox|jest|vitest|mocha|ctest|rspec|phpunit|bats)\b"
            r"|\bgo\s+test\b|\bdotnet\s+test\b|\bflutter\s+test\b|\bcargo\s+test\b|\bdart\s+test\b"
        ),
        RiskLevel.SAFE,
        "runs the project's test suite",
    ),
    _Rule(
        "linter",
        re.compile(
            r"(?:^|[;&|]\s*)(?:python(?:3)?(?:\.\d+)?\s+-m\s+)?(?:ruff|black|flake8|pylint|isort|mypy|pyright|eslint|prettier|golangci-lint|gofmt|goimports|tsc|shellcheck|hadolint|yamllint|stylelint|clippy|rubocop)\b"
        ),
        RiskLevel.SAFE,
        "runs a linter or formatter check",
    ),
    _Rule(
        "build-and-analyze",
        re.compile(
            r"\bgo\s+build\b|\bdotnet\s+build\b|\bcargo\s+build\b"
            r"|\bmvn\s+(?:test|package|verify|compile)\b"
            r"|\bgradle\w*\s+(?:build|test|check|assemble)\b|\./gradlew\s+(?:build|test|check)\b"
            r"|\bflutter\s+(?:analyze|build)\b|\bdart\s+analyze\b"
        ),
        RiskLevel.SAFE,
        "builds or statically checks the project",
    ),
    _Rule(
        "read-only-docker",
        re.compile(
            r"\bdocker\s+(?:ps|images|inspect|version|info|stats|logs)\b|\bdocker[\s-]compose\s+(?:config|ps|logs|images)\b"
        ),
        RiskLevel.SAFE,
        "read-only Docker inspection",
    ),
    _Rule(
        "read-only-k8s",
        re.compile(r"\bkubectl\s+(?:get|describe|logs|version|config\s+view)\b"),
        RiskLevel.SAFE,
        "read-only Kubernetes inspection",
    ),
    _Rule(
        "read-only-migration",
        re.compile(
            r"\b(?:alembic\s+(?:current|heads|history|check)|prisma\s+migrate\s+status|flyway\s+info)\b"
        ),
        RiskLevel.SAFE,
        "read-only migration inspection",
    ),
    _Rule(
        "read-only-shell",
        re.compile(
            r"(?:^|[;&|]\s*)(?:ls|dir|cat|type|head|tail|wc|find|rg|grep|jq|tree|du|df|pwd|echo|which|where|whoami)\b"
        ),
        RiskLevel.SAFE,
        "read-only shell inspection",
    ),
    _Rule(
        "help-or-version",
        re.compile(r"\s--help\b(?:\s|$)|\s-h\s*$|\bversion\s*$"),
        RiskLevel.SAFE,
        "prints help or version information",
    ),
)


@dataclass(frozen=True)
class _SegmentRisk:
    level: RiskLevel = RiskLevel.SAFE
    reasons: tuple[str, ...] = ()
    rules: tuple[str, ...] = ()


def classify_command(command: str, *, script_body: str | None = None) -> CommandRisk:
    """Classify a command and, optionally, the body of its script."""
    return classify_many([command] + ([script_body] if script_body else []))


def classify_many(commands: list[str]) -> CommandRisk:
    """Classify several related command strings as a single result.

    The result is the highest risk found across all inputs. Pipeline rules are
    evaluated on the whole command *before* segment splitting so patterns such
    as ``curl … | sh`` are detected.
    """
    level = RiskLevel.SAFE
    reasons: list[str] = []
    rules: list[str] = []
    for raw in commands:
        if not raw or not raw.strip():
            continue
        normalized = normalize_whitespace(raw)
        whole = _regex_risk(normalized)
        merged = [whole, *(_classify_segment(segment) for segment in _split_segments(normalized))]
        for part in merged:
            if part.level > level:
                level = part.level
            for rule_id, reason in zip(part.rules, part.reasons, strict=True):
                if rule_id not in rules:
                    rules.append(rule_id)
                    reasons.append(reason)
    return CommandRisk(level=level, reasons=tuple(reasons), matched_rules=tuple(rules))


def _split_segments(command: str) -> list[str]:
    """Split a shell-ish command into independently classified segments."""
    segments = re.split(r"\s*(?:&&|\|\||;|\||\n)\s*", command)
    return [segment for segment in (s.strip() for s in segments) if segment]


def _regex_risk(command: str) -> _SegmentRisk:
    """Apply all regex rules, keeping safe matches only when nothing else hits."""
    review_or_dangerous: list[_Rule] = []
    safe: list[_Rule] = []
    for rule in _RULES:
        if rule.pattern.search(command):
            if rule.level is RiskLevel.SAFE:
                safe.append(rule)
            else:
                review_or_dangerous.append(rule)
    if review_or_dangerous:
        level = max(rule.level for rule in review_or_dangerous)
        return _SegmentRisk(
            level=level,
            reasons=tuple(rule.reason for rule in review_or_dangerous),
            rules=tuple(rule.id for rule in review_or_dangerous),
        )
    return _SegmentRisk(
        level=RiskLevel.SAFE,
        reasons=tuple(rule.reason for rule in safe),
        rules=tuple(rule.id for rule in safe),
    )


def _tokenize(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=True)
    except ValueError:
        return command.split()


def _first_binary(tokens: list[str]) -> str | None:
    for token in tokens:
        if token in ("sudo", "env", "nohup", "command", "time"):
            continue
        if token.startswith("-"):
            continue
        return token.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return None


def _classify_segment(segment: str) -> _SegmentRisk:
    """Token-aware classification for the special cases regexes cannot cover."""
    regex_result = _regex_risk(segment)
    tokens = _tokenize(segment)
    binary = _first_binary(tokens)
    if binary is None:
        return regex_result

    level = regex_result.level
    reasons = list(regex_result.reasons)
    rules = list(regex_result.rules)

    def bump(rule_id: str, reason: str, at_least: RiskLevel = RiskLevel.REVIEW) -> None:
        nonlocal level
        if rule_id not in rules:
            rules.append(rule_id)
            reasons.append(reason)
        level = max(level, at_least)

    if binary in {"npm", "pnpm", "yarn", "bun"}:
        subcommand = next((t for t in tokens[1:] if not t.startswith("-")), "")
        if subcommand == "run" or (binary == "yarn" and subcommand not in {"install", "add", "up"}):
            bump("package-script", "runs a script defined by the repository", RiskLevel.SAFE)
        elif subcommand in {"publish", "unpublish", "deprecate"}:
            bump("publish", "publishes a package")
        elif subcommand in {"install", "add", "update", "upgrade", "ci", "up"}:
            bump("dependency-install", "resolves dependencies and may execute lifecycle scripts")
    elif binary in {"poetry", "uv", "pdm", "hatch", "flit"} and len(tokens) > 1:
        subcommand = tokens[1]
        if subcommand == "run" and len(tokens) > 2:
            inner = _classify_segment(" ".join(tokens[2:]))
            if inner.level > level:
                level = inner.level
            for rule_id, reason in zip(inner.rules, inner.reasons, strict=True):
                if rule_id not in rules:
                    rules.append(rule_id)
                    reasons.append(reason)
        elif subcommand in {"publish", "upload"}:
            bump("publish", "publishes a package")
        elif subcommand in {"install", "add", "sync", "update", "lock"}:
            bump("dependency-install", "resolves dependencies and may execute lifecycle scripts")
    elif binary in {"python", "python3", "py"} and len(tokens) > 2 and tokens[1] == "-m":
        inner = _classify_segment(" ".join(tokens[2:]))
        if inner.level > level:
            level = inner.level
        for rule_id, reason in zip(inner.rules, inner.reasons, strict=True):
            if rule_id not in rules:
                rules.append(rule_id)
                reasons.append(reason)
    elif binary == "dotnet" and len(tokens) > 1:
        if tokens[1] == "ef":
            bump("migration", "applies or reverts database migrations")
        elif tokens[1] == "nuget":
            bump("publish", "publishes a package")
    elif (
        binary == "alembic" and len(tokens) > 1 and tokens[1] in {"upgrade", "downgrade", "stamp"}
    ) or (binary == "prisma" and len(tokens) > 1 and tokens[1] in {"migrate", "db"}):
        bump("migration", "applies or reverts database migrations")
    elif binary == "docker" and len(tokens) > 2 and tokens[1] == "compose":
        subcommand = tokens[2]
        if subcommand in _COMPOSE_READ_ONLY:
            bump("read-only-docker", "read-only Docker inspection", RiskLevel.SAFE)
        else:
            bump("docker-compose", "starts or manages containers defined by the repository")
    elif binary == "docker" and len(tokens) > 1:
        if tokens[1] in _DOCKER_READ_ONLY:
            bump("read-only-docker", "read-only Docker inspection", RiskLevel.SAFE)
        else:
            bump("docker", "invokes Docker and may run repository-provided images or Dockerfiles")
    elif binary == "git" and len(tokens) > 1:
        subcommand = tokens[1]
        if subcommand in _SAFE_GIT_SUBCOMMANDS:
            bump("read-only-git", "read-only git command", RiskLevel.SAFE)
        elif subcommand in _REVIEW_GIT_SUBCOMMANDS:
            bump("git-mutate", "mutates repository or remote state")
    elif binary == "make" and len(tokens) > 1:
        target = next((t for t in tokens[1:] if not t.startswith("-")), "")
        if target in _MAKE_SAFE_TARGETS:
            bump("build-and-analyze", f"runs the standard '{target}' make target", RiskLevel.SAFE)

    # Known-safe tooling should not be dragged up by a generic rule, unless a
    # dangerous rule matched.
    if level is RiskLevel.REVIEW and binary in _TEST_RUNNERS | _LINTERS:
        level = RiskLevel.SAFE
    return _SegmentRisk(
        level=level, reasons=tuple(dict.fromkeys(reasons)), rules=tuple(dict.fromkeys(rules))
    )
