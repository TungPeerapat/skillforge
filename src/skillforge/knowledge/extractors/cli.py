"""Bounded parser for official CLI references/help snapshots; no subprocesses.

Recognizes explicit Usage/Options/Commands/Arguments blocks and typed command
headings. It does not treat arbitrary examples as supported CLI declarations.
"""

from __future__ import annotations

import json
import re

from pydantic import JsonValue, TypeAdapter

from skillforge.knowledge.extractors.tool_base import (
    ToolExtractionResult,
    reference_within_budget,
    safe_metadata_value,
    verified_reference,
)
from skillforge.knowledge.normalize import NormalizedDocument
from skillforge.models.knowledge import (
    CapabilityDraft,
    CapabilityKind,
    CliArgumentSpec,
    CliOptionSpec,
    CliSpec,
    KnowledgeSource,
    KnowledgeTarget,
    ToolProvenance,
)
from skillforge.security.redaction import is_sensitive_name, redact_text

_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_OPTION = re.compile(r"(?<!\S)(--[A-Za-z][\w-]*|-[A-Za-z0-9])(?![\w-])")
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_TOOLS = {"pytest": "pytest", "ruff": "ruff", "next": "next", "dotnet": "dotnet", ".net": "dotnet"}


def clean_help(text: str) -> str:
    text = _ANSI.sub("", text)
    return "".join(
        char for char in text if char in "\n\t" or (ord(char) >= 32 and ord(char) != 127)
    )


def _default(description: str) -> tuple[bool, JsonValue]:
    match = re.search(r"\[default:\s*(.*?)\]\s*$", description, re.I)
    if match is None:
        return False, None
    raw = match[1].strip()
    try:
        value = json.loads(raw, parse_constant=str)
    except ValueError:
        if raw.startswith(("[", "{", '"')):
            return False, None
        value = raw
    return True, TypeAdapter(JsonValue).validate_python(value)


class CliReferenceExtractor:
    """Supported grammar is deliberately narrow; ambiguous lines produce gaps."""

    supported_technologies = frozenset(_TOOLS)

    def extract_reference(
        self,
        document: NormalizedDocument,
        source: KnowledgeSource,
        target: KnowledgeTarget,
        *,
        max_capabilities: int = 20_000,
        max_quote_chars: int = 200,
    ) -> ToolExtractionResult:
        result = ToolExtractionResult()
        if not verified_reference(document, source, target, require_ecosystem=True):
            result.diagnostics.append(
                "CLI reference rejected: source/target/version proof mismatch"
            )
            return result
        if max_capabilities < 1 or not 0 <= max_quote_chars <= 1000:
            raise ValueError("invalid CLI extraction budgets")
        if not reference_within_budget(document):
            result.diagnostics.append("CLI reference character budget exceeded")
            return result
        tool = _TOOLS.get(target.technology.lower())
        if tool is None:
            result.diagnostics.append("no configured CLI identity for this technology")
            return result
        records: dict[tuple[str, CapabilityKind], CapabilityDraft] = {}

        def add(
            kind: CapabilityKind,
            name: str,
            qualified: str,
            parent: str | None,
            locator: str,
            description: str = "",
            **metadata: object,
        ) -> CapabilityDraft | None:
            key = (qualified, kind)
            if key in records:
                return records[key]
            if len(records) >= max_capabilities:
                result.diagnostics.append("CLI capability budget reached")
                return None
            draft = CapabilityDraft(
                technology=target.technology,
                ecosystem=target.ecosystem,
                version=target.version,
                kind=kind,
                name=name,
                qualified_name=qualified,
                parent_qualified=parent,
                description=redact_text(description)[:max_quote_chars],
                sources=[source.id],
                tool_provenance=[
                    ToolProvenance(
                        source_id=source.id,
                        locator=locator,
                        extractor="cli-reference-v1",
                    )
                ],
                **metadata,  # type: ignore[arg-type]
            )
            records[key] = draft
            return draft

        def command(path: list[str], locator: str, usage: str | None = None) -> None:
            for length in range(1, len(path) + 1):
                part = path[:length]
                draft = add(
                    CapabilityKind.CLI_COMMAND if length == 1 else CapabilityKind.CLI_SUBCOMMAND,
                    part[-1],
                    "cli:" + " ".join(part),
                    "cli:" + " ".join(part[:-1]) if length > 1 else None,
                    locator,
                    cli=CliSpec(command_path=part, usage=usage if length == len(path) else None),
                )
                if draft and draft.cli and usage and length == len(path):
                    draft.cli.usage = redact_text(usage)[:1000]

        current: list[str] | None = None
        current_usage: str | None = None
        block = ""
        previous: CapabilityDraft | None = None
        for section_number, section in enumerate(document.sections):
            heading = section.heading.strip().strip("`")
            if heading.lower() in {"example", "examples", "tutorial", "getting started"}:
                current = None
                block = ""
                previous = None
                continue
            explicit = re.fullmatch(r"(?:command|subcommand)\s+(.+)", heading, re.I)
            if explicit:
                path = explicit[1].strip("`").split()
                if (
                    path
                    and path[0].lower() == tool
                    and all(_TOKEN.fullmatch(part) for part in path)
                ):
                    current = path
                    current_usage = None
                    command(path, f"section:{section_number}")
            text = clean_help(section.text)
            # Code blocks are parsed only inside explicit CLI-reference headings.
            if explicit or heading.lower() in {"cli reference", "command reference", "usage"}:
                text += "\n" + "\n".join(section.code_blocks)
            text = clean_help(text)
            lines = (
                [heading + ":"]
                if heading.lower()
                in {
                    "options",
                    "commands",
                    "arguments",
                    "flags",
                }
                else []
            ) + text.splitlines()
            for line_number, line in enumerate(lines):
                locator = f"section:{section_number}:line:{line_number + 1}"
                stripped = line.strip()
                if not stripped:
                    continue
                usage = re.match(r"usage:\s*(.+)", stripped, re.I)
                if usage:
                    tokens = usage[1].split()
                    if not tokens or tokens[0].lower() != tool:
                        result.diagnostics.append(f"unrecognized executable at {locator}")
                        current = None
                        continue
                    path = [tool]
                    for token in tokens[1:]:
                        if _TOKEN.fullmatch(token) and not token.isupper():
                            path.append(token)
                        else:
                            break
                    current = path
                    current_usage = usage[1]
                    command(path, locator, usage[1])
                    block = ""
                    previous = None
                    continue
                if stripped.lower().rstrip(":") in {"options", "flags", "commands", "arguments"}:
                    block = stripped.lower().rstrip(":")
                    previous = None
                    continue
                if current is None:
                    continue
                if block in {"options", "flags"} and stripped.startswith("-"):
                    declaration, *description_parts = re.split(r"\s{2,}", stripped, maxsplit=1)
                    matches = list(_OPTION.finditer(declaration.replace(",", " ")))
                    if not matches:
                        result.diagnostics.append(f"unparsed option at {locator}")
                        continue
                    names = list(dict.fromkeys(match[1] for match in matches))
                    # Refuse partial parses of unsupported syntax.
                    tail = declaration.replace(",", " ")[matches[-1].end() :].strip().lstrip("=")
                    value_name = tail.strip("[]<>") or None
                    if value_name and not re.fullmatch(r"[A-Za-z][\w.-]*", value_name):
                        result.diagnostics.append(f"unsupported option value syntax at {locator}")
                        continue
                    takes_value = value_name is not None
                    description = redact_text(description_parts[0] if description_parts else "")
                    known, default = _default(description)
                    primary = next((name for name in names if name.startswith("--")), names[0])
                    if known:
                        default = safe_metadata_value(
                            default, sensitive=is_sensitive_name(primary.lstrip("-"))
                        )
                        if is_sensitive_name(primary.lstrip("-")):
                            description = re.sub(
                                r"\[default:.*", "[default: redacted]", description, flags=re.I
                            )
                    previous = add(
                        CapabilityKind.CLI_OPTION if takes_value else CapabilityKind.CLI_FLAG,
                        primary,
                        "cli:" + " ".join(current) + " " + primary,
                        "cli:" + " ".join(current),
                        locator,
                        description,
                        option=CliOptionSpec(
                            names=names,
                            takes_value=takes_value,
                            value_name=value_name,
                            default_known=known,
                            default=default,
                        ),
                    )
                elif block in {"commands", "arguments"} and not stripped.startswith("-"):
                    name, *description_parts = re.split(r"\s{2,}", stripped, maxsplit=1)
                    aliases = [part.strip() for part in name.split(",")]
                    if not all(_TOKEN.fullmatch(part) for part in aliases):
                        result.diagnostics.append(f"unsupported {block} line at {locator}")
                        continue
                    name = aliases[0]
                    description = description_parts[0] if description_parts else ""
                    if block == "commands":
                        command([*current, name], locator)
                        previous = records.get(
                            ("cli:" + " ".join([*current, name]), CapabilityKind.CLI_SUBCOMMAND)
                        )
                        if previous and previous.cli:
                            previous.cli.aliases = [
                                " ".join([*current, alias]) for alias in aliases[1:]
                            ]
                            previous.description = redact_text(description)[:max_quote_chars]
                    else:
                        argument = CliArgumentSpec(name=name)
                        if current_usage:
                            optional = re.search(
                                r"\[" + re.escape(name) + r"(\.\.\.)?\]", current_usage
                            )
                            mandatory = re.search(
                                r"<" + re.escape(name) + r">(\.\.\.)?", current_usage
                            )
                            if optional:
                                argument.required = False
                                argument.repeatable = bool(optional[1])
                            elif mandatory:
                                argument.required = True
                                argument.repeatable = bool(mandatory[1])
                        previous = add(
                            CapabilityKind.CLI_ARGUMENT,
                            name,
                            "cli:" + " ".join(current) + " <" + name + ">",
                            "cli:" + " ".join(current),
                            locator,
                            description,
                            argument=argument,
                        )
                elif previous is not None and line[:1].isspace():
                    if previous.option:
                        known, default = _default(redact_text(stripped))
                        if known:
                            previous.option.default_known = True
                            previous.option.default = safe_metadata_value(
                                default,
                                sensitive=is_sensitive_name(previous.name.lstrip("-")),
                            )
                            if is_sensitive_name(previous.name.lstrip("-")):
                                stripped = re.sub(
                                    r"\[default:.*", "[default: redacted]", stripped, flags=re.I
                                )
                    previous.description = redact_text(previous.description + " " + stripped)[
                        :max_quote_chars
                    ]
                elif stripped.endswith(":"):
                    block = ""
                    previous = None
        result.drafts = list(records.values())
        if not records:
            result.diagnostics.append("no supported CLI declarations found")
        result.diagnostics = sorted(set(result.diagnostics))
        return result
