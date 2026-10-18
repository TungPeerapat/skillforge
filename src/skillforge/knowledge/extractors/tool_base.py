"""Shared proof/budget guards for K10 tool references (never executes tools)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, JsonValue, TypeAdapter

from skillforge.knowledge.normalize import NormalizedDocument
from skillforge.models.knowledge import CapabilityDraft, KnowledgeSource, KnowledgeTarget
from skillforge.security.redaction import is_sensitive_name, redact_text


def safe_metadata_value(value: Any, *, sensitive: bool = False) -> JsonValue:
    """Redact structured defaults/choices, not only surrounding prose."""
    if isinstance(value, str):
        if sensitive and value:
            return "[redacted]"
        return redact_text(value)
    if isinstance(value, list):
        return [safe_metadata_value(item, sensitive=sensitive) for item in value]
    if isinstance(value, dict):
        return {
            str(key): safe_metadata_value(item, sensitive=sensitive or is_sensitive_name(str(key)))
            for key, item in value.items()
        }
    return TypeAdapter(JsonValue).validate_python(value)


def merge_tool_metadata(before: BaseModel, after: BaseModel) -> tuple[BaseModel, list[str]]:
    """Fill unknown fields without replacing established facts; report conflicts."""
    merged = before.model_copy(deep=True)
    conflicts: list[str] = []
    if type(before) is not type(after):
        return merged, ["metadata_type"]
    for name in type(before).model_fields:
        if name in {"default", "default_known"}:
            continue
        old, new = getattr(before, name), getattr(after, name)
        if old is None and new is not None:
            setattr(merged, name, new)
        elif name in {"names", "aliases"}:
            setattr(merged, name, list(dict.fromkeys([*old, *new])))
        elif new is not None and old != new:
            conflicts.append(name)
    old_values, new_values = before.model_dump(), after.model_dump()
    if "default_known" in old_values:
        if not old_values["default_known"] and new_values["default_known"]:
            merged = merged.model_copy(
                update={"default_known": True, "default": new_values["default"]}
            )
        elif (
            old_values["default_known"]
            and new_values["default_known"]
            and old_values["default"] != new_values["default"]
        ):
            conflicts.append("default")
    return merged, conflicts


@dataclass
class ToolExtractionResult:
    drafts: list[CapabilityDraft] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)


def reference_within_budget(document: NormalizedDocument) -> bool:
    return (
        sum(
            len(section.heading) + len(section.text) + sum(map(len, section.code_blocks))
            for section in document.sections
        )
        <= 2 * 1024 * 1024
    )


def verified_reference(
    document: NormalizedDocument,
    source: KnowledgeSource,
    target: KnowledgeTarget,
    *,
    require_ecosystem: bool = False,
) -> bool:
    """A matching URL alone is insufficient: source authority must be verified."""
    return (
        source.capability_eligible
        and document.version_match.is_exact
        and document.source_id == source.id
        and document.url == source.url
        and source.technology.lower() == target.technology.lower()
        and source.version == target.version
        and (
            source.ecosystem == target.ecosystem
            if require_ecosystem
            else source.ecosystem in (None, target.ecosystem)
        )
    )


class ToolCapabilityExtractor(Protocol):
    def extract_reference(
        self,
        document: NormalizedDocument,
        source: KnowledgeSource,
        target: KnowledgeTarget,
        *,
        max_capabilities: int = 20_000,
        max_quote_chars: int = 200,
    ) -> ToolExtractionResult: ...
