"""Validation checks.

Each check receives a :class:`ValidationTarget` and appends findings to a
:class:`ValidationResult`. Checks never raise on malformed input: a skill is
untrusted data too.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

from skillforge.models import (
    COMPATIBILITY_MAX,
    DESCRIPTION_MAX,
    SKILL_MD_MAX_LINES,
    SKILL_MD_TOKEN_WARN,
    SKILL_NAME_MAX,
    SUPPORTED_RESOURCE_DIRS,
    FindingSeverity,
    RepositoryProfile,
    SkillBundle,
    SkillManifest,
    ValidationResult,
    is_valid_skill_name,
)
from skillforge.security.paths import is_absolute_local_path
from skillforge.security.secrets import find_secrets
from skillforge.utils.fs import sha256_text
from skillforge.utils.markdown import fenced_code_blocks, headings, referenced_skill_paths
from skillforge.utils.text import strip_command_prefix
from skillforge.utils.tokens import estimate_tokens

_SHELL_BLOCK_LANGUAGES = frozenset(
    {
        "bash",
        "sh",
        "shell",
        "console",
        "terminal",
        "zsh",
        "powershell",
        "ps1",
        "cmd",
        "bat",
        "dockerfile",
    }
)
_PLACEHOLDER_RE = re.compile(
    r"<[A-Za-z][A-Za-z0-9_.:-]*>|\$\{[A-Za-z_][A-Za-z0-9_]*\}|\{\{[^}]+\}\}"
)
_LOCAL_HOST_RE = re.compile(r"(?i)\b(localhost|127\.0\.0\.1|0\.0\.0\.0)\b")
_SECRET_PATH_RE = re.compile(r"(?i)(\.env($|[^.\w])|id_rsa|\.pem\b|\.key\b)")


@dataclass
class ValidationTarget:
    """A skill under validation plus optional repository context."""

    bundle: SkillBundle
    target: str
    directory: Path | None = None
    manifest: SkillManifest | None = None
    profile: RepositoryProfile | None = None
    strict: bool = False
    #: Raw SKILL.md text when the skill was loaded from disk (hashes must match
    #: the file on disk, not a re-rendered version of it).
    raw_skill_md: str = ""

    @property
    def contents(self) -> dict[str, str]:
        contents = self.bundle.all_contents()
        if self.raw_skill_md:
            contents["SKILL.md"] = self.raw_skill_md
        return contents

    @property
    def body(self) -> str:
        return self.bundle.body

    @property
    def is_generated(self) -> bool:
        return self.manifest is not None


def command_lines_from_body(body: str) -> list[str]:
    """Extract candidate command lines from shell code blocks in a SKILL.md body."""
    commands: list[str] = []
    for block in fenced_code_blocks(body):
        if block.lang and block.lang not in _SHELL_BLOCK_LANGUAGES:
            continue
        for raw in block.content.splitlines():
            line = strip_command_prefix(raw)
            if not line or line.startswith("#"):
                continue
            if len(line) > 300:
                continue
            commands.append(line)
    return commands


# ------------------------------------------------------------------ structure


def check_frontmatter(target: ValidationTarget, result: ValidationResult) -> None:
    result.checked.append("frontmatter")
    metadata = target.bundle.metadata
    if not metadata.name:
        result.add("metadata.missing-name", FindingSeverity.ERROR, "SKILL.md has no name")
    elif not is_valid_skill_name(metadata.name):
        result.add(
            "metadata.invalid-name",
            FindingSeverity.ERROR,
            f"name '{metadata.name}' must match ^[a-z0-9]+(-[a-z0-9]+)*$ and be at most "
            f"{SKILL_NAME_MAX} characters",
            location="SKILL.md",
        )
    if target.directory is not None and metadata.name != target.directory.name:
        result.add(
            "metadata.name-directory-mismatch",
            FindingSeverity.ERROR,
            f"frontmatter name '{metadata.name}' does not match directory "
            f"'{target.directory.name}' (required by the Agent Skills spec and OpenCode)",
            location="SKILL.md",
        )
    if not metadata.description.strip():
        result.add("metadata.missing-description", FindingSeverity.ERROR, "description is empty")
    elif len(metadata.description) > DESCRIPTION_MAX:
        result.add(
            "metadata.description-too-long",
            FindingSeverity.ERROR,
            f"description is {len(metadata.description)} characters (maximum {DESCRIPTION_MAX})",
        )
    elif len(metadata.description) < 40:
        result.add(
            "metadata.description-short",
            FindingSeverity.WARNING,
            "description is very short; describe both what the skill does and when to use it",
        )
    if metadata.compatibility and len(metadata.compatibility) > COMPATIBILITY_MAX:
        result.add(
            "metadata.compatibility-too-long",
            FindingSeverity.ERROR,
            f"compatibility exceeds {COMPATIBILITY_MAX} characters",
        )
    unknown_keys = set(metadata.metadata) - {
        "generator",
        "generator-version",
        "source-repository",
        "certainty",
        "author",
        "version",
    }
    if unknown_keys:
        result.add(
            "metadata.unknown-keys",
            FindingSeverity.INFO,
            "metadata contains non-standard keys: " + ", ".join(sorted(unknown_keys)),
        )


def check_body(target: ValidationTarget, result: ValidationResult) -> None:
    result.checked.append("body")
    body = target.body
    if not body.strip():
        result.add("body.empty", FindingSeverity.ERROR, "SKILL.md body is empty")
        return
    if not headings(body):
        result.add(
            "body.no-headings",
            FindingSeverity.WARNING,
            "SKILL.md body has no Markdown headings",
        )
    if "@" in body and "SKILL.md" not in body:
        result.add(  # pragma: no cover - extremely unusual
            "body.unreferenced",
            FindingSeverity.INFO,
            "body does not reference SKILL.md or references; consider adding pointers",
        )


def check_size(target: ValidationTarget, result: ValidationResult) -> None:
    result.checked.append("size")
    lines = len(target.body.splitlines())
    tokens = estimate_tokens(target.body)
    if lines > SKILL_MD_MAX_LINES * 2:
        result.add(
            "size.body-too-large",
            FindingSeverity.ERROR,
            f"SKILL.md body is {lines} lines; split detailed material into references/",
        )
    elif lines > SKILL_MD_MAX_LINES:
        result.add(
            "size.body-large",
            FindingSeverity.WARNING,
            f"SKILL.md body is {lines} lines (recommended maximum {SKILL_MD_MAX_LINES})",
        )
    if tokens > SKILL_MD_TOKEN_WARN * 2:
        result.add(
            "size.body-tokens",
            FindingSeverity.WARNING,
            f"SKILL.md body is roughly {tokens} tokens; progressive disclosure recommends under "
            f"{SKILL_MD_TOKEN_WARN}",
        )
    total_tokens = target.bundle.total_tokens()
    if total_tokens > SKILL_MD_TOKEN_WARN * 6:
        result.add(
            "size.skill-total",
            FindingSeverity.INFO,
            f"the whole skill is roughly {total_tokens} tokens across all files",
        )


_DUPLICATE_RE = re.compile(r"^\s*(?:[-*]|\d+\.)\s+(?P<text>.+)$")

#: Structured field lines repeat by design (one per command/unknown); they are
#: not "duplicate instructions".
_FIELD_PREFIXES = (
    "why unknown:",
    "check:",
    "where it comes from:",
    "risk:",
    "source:",
    "evidence:",
    "run from:",
    "review because:",
    "note:",
    "migrations:",
    "entry points:",
    "specifications:",
    "route directories:",
    "migration directories:",
)


def check_duplicate_instructions(target: ValidationTarget, result: ValidationResult) -> None:
    result.checked.append("duplicates")
    seen: dict[str, int] = {}
    for line in target.body.splitlines():
        match = _DUPLICATE_RE.match(line)
        if not match:
            continue
        text = " ".join(match.group("text").split()).lower()
        if len(text) < 25:
            continue
        if any(text.startswith(prefix) for prefix in _FIELD_PREFIXES):
            continue
        seen[text] = seen.get(text, 0) + 1
    duplicates = {text: count for text, count in seen.items() if count > 1}
    if duplicates:
        sample = sorted(duplicates)[0]
        result.add(
            "duplicates.repeated-instructions",
            FindingSeverity.WARNING,
            f"{len(duplicates)} instruction(s) appear more than once, e.g. '{sample[:80]}'",
        )


# ----------------------------------------------------------------- references


def check_references(target: ValidationTarget, result: ValidationResult) -> None:
    result.checked.append("references")
    referenced = referenced_skill_paths(target.body)
    existing = set(target.bundle.files)
    for path in sorted(referenced):
        if path not in existing:
            severity = FindingSeverity.ERROR
            message = f"SKILL.md references '{path}' but the file does not exist"
            if path.startswith("scripts/"):
                message = f"SKILL.md references script '{path}' which is missing"
            result.add("references.broken-link", severity, message, location="SKILL.md")
    for path in sorted(existing - referenced):
        if not path.startswith(("scripts/", "assets/")):
            result.add(
                "references.unreferenced-file",
                FindingSeverity.WARNING,
                f"file '{path}' is not referenced from SKILL.md; agents may never load it",
                location=path,
            )
    for path in sorted(existing):
        root = path.split("/", 1)[0]
        if root not in SUPPORTED_RESOURCE_DIRS:
            result.add(
                "references.unexpected-directory",
                FindingSeverity.INFO,
                f"'{path}' is outside the conventional references/, scripts/, assets/ layout",
                location=path,
            )


def check_scripts_syntax(target: ValidationTarget, result: ValidationResult) -> None:
    result.checked.append("scripts")
    for path, content in sorted(target.contents.items()):
        if not path.startswith("scripts/"):
            continue
        if path.endswith(".py"):
            try:
                ast.parse(content, filename=path)
            except SyntaxError as exc:
                result.add(
                    "scripts.python-syntax",
                    FindingSeverity.ERROR,
                    f"{path} is not valid Python: {exc.msg} (line {exc.lineno})",
                    location=path,
                )
        if not content.strip():
            result.add("scripts.empty", FindingSeverity.ERROR, f"{path} is empty", location=path)


# ------------------------------------------------------------------- security


def check_absolute_paths(target: ValidationTarget, result: ValidationResult) -> None:
    result.checked.append("paths")
    for path, content in sorted(target.contents.items()):
        for number, line in enumerate(content.splitlines(), start=1):
            if not line.strip() or line.strip().startswith("http"):
                continue
            if is_absolute_local_path(line):
                result.add(
                    "security.absolute-path",
                    FindingSeverity.ERROR,
                    f"absolute local path found: '{line.strip()[:100]}'",
                    location=f"{path}:{number}",
                    hint="generated skills must be portable across machines",
                )


def check_secrets(target: ValidationTarget, result: ValidationResult) -> None:
    result.checked.append("secrets")
    for path, content in sorted(target.contents.items()):
        for finding in find_secrets(content):
            result.add(
                "security.possible-secret",
                FindingSeverity.ERROR,
                f"{finding.description} detected in {path} (line {finding.line}, "
                f"{finding.preview}); never ship credentials inside a skill",
                location=path,
            )


def check_command_safety(target: ValidationTarget, result: ValidationResult) -> None:
    result.checked.append("command-safety")
    from skillforge.security.command_risk import classify_command

    for path, content in sorted(target.contents.items()):
        if not (path == "SKILL.md" or path.startswith(("references/", "scripts/"))):
            continue
        for number, line in enumerate(content.splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith(("#", "|", "-", ">", "`")):
                continue
            if "<" in stripped and ">" in stripped:
                continue
            risk = classify_command(stripped)
            if risk.is_dangerous:
                result.add(
                    "security.dangerous-command",
                    FindingSeverity.ERROR,
                    f"destructive command pattern in {path}: '{stripped[:120]}' "
                    f"({'; '.join(risk.reasons)})",
                    location=f"{path}:{number}",
                    hint="SkillForge must never embed destructive commands; remove or gate it",
                )
    for command in command_lines_from_body(target.body):
        if _PLACEHOLDER_RE.search(command):
            result.add(
                "security.command-placeholder",
                FindingSeverity.WARNING,
                f"command contains an unresolved placeholder: '{command[:120]}'",
                location="SKILL.md",
            )
        if "sudo" in command.split():
            result.add(
                "security.sudo",
                FindingSeverity.WARNING,
                f"command requires elevated privileges: '{command[:120]}'",
                location="SKILL.md",
            )
        if _LOCAL_HOST_RE.search(command):
            result.add(
                "assumptions.local-service",
                FindingSeverity.INFO,
                f"command assumes a local service is running: '{command[:120]}'",
                location="SKILL.md",
            )
        if _SECRET_PATH_RE.search(command) and ("cat" in command or "type " in command):
            result.add(
                "security.secret-file-read",
                FindingSeverity.WARNING,
                f"command reads a secret-looking file: '{command[:120]}'",
                location="SKILL.md",
            )


# ------------------------------------------------------------------- evidence


def _normalise(command: str) -> str:
    return " ".join(command.split()).strip().lower()


def check_command_evidence(target: ValidationTarget, result: ValidationResult) -> None:
    result.checked.append("evidence")
    commands = command_lines_from_body(target.body)
    if not commands:
        result.add(
            "evidence.no-commands",
            FindingSeverity.INFO,
            "SKILL.md contains no shell commands",
        )
        return
    if target.profile is None:
        result.add(
            "evidence.unverified",
            FindingSeverity.INFO,
            f"{len(commands)} command(s) could not be checked against repository evidence "
            "(no analysis profile provided)",
        )
        return
    known = [_normalise(command.command) for command in target.profile.commands]
    for command in commands:
        needle = _normalise(command)
        if any(
            needle == candidate
            or needle.startswith(candidate + " ")
            or (candidate.startswith(needle + " ") and len(needle) > 5)
            for candidate in known
        ):
            continue
        if _invokes_bundle_file(needle, target):
            continue
        result.add(
            "evidence.command-without-source",
            FindingSeverity.WARNING,
            f"command has no repository evidence: '{command[:120]}'",
            location="SKILL.md",
            hint="commands must come from a manifest, script, Makefile, CI file, or documentation",
        )


def _invokes_bundle_file(command: str, target: ValidationTarget) -> bool:
    """True when a command runs a file that ships inside this skill."""
    tokens = command.replace("'", " ").replace('"', " ").split()
    for token in tokens:
        cleaned = token.lstrip("./")
        if cleaned in target.bundle.files:
            return True
    return False


# ------------------------------------------------------------------- manifest


def check_manifest(target: ValidationTarget, result: ValidationResult) -> None:
    result.checked.append("manifest")
    manifest = target.manifest
    if manifest is None:
        if target.strict:
            result.add(
                "manifest.missing",
                FindingSeverity.WARNING,
                "no .skillforge.json found; this skill was not generated by SkillForge",
            )
        else:
            result.add(
                "manifest.missing",
                FindingSeverity.INFO,
                "no .skillforge.json found; provenance checks were skipped",
            )
        return
    if manifest.skill != target.bundle.metadata.name:
        result.add(
            "manifest.name-mismatch",
            FindingSeverity.WARNING,
            f"manifest records skill '{manifest.skill}' but frontmatter says "
            f"'{target.bundle.metadata.name}'",
        )
    contents = target.contents
    for path, recorded_hash in sorted(manifest.file_hashes.items()):
        if path not in contents:
            result.add(
                "manifest.file-missing",
                FindingSeverity.WARNING,
                f"manifest lists '{path}' but the file is missing",
                location=path,
            )
            continue
        if sha256_text(contents[path]) != recorded_hash:
            result.add(
                "manifest.file-modified",
                FindingSeverity.WARNING,
                f"'{path}' differs from the generated version (edited by hand or corrupted)",
                location=path,
            )
    extra = sorted(set(contents) - set(manifest.file_hashes))
    if extra:
        result.add(
            "manifest.extra-files",
            FindingSeverity.INFO,
            "files added since generation: " + ", ".join(extra),
        )


#: All checks, in display order.
DEFAULT_CHECKS = (
    check_frontmatter,
    check_body,
    check_size,
    check_duplicate_instructions,
    check_references,
    check_scripts_syntax,
    check_absolute_paths,
    check_secrets,
    check_command_safety,
    check_command_evidence,
    check_manifest,
)
