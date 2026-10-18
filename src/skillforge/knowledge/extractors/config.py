"""Official config reference/JSON-schema extraction; no config evaluation.

Only explicit config headings or supplied version-proven schema documents are
used. Local JSON pointers are bounded/cycle-checked; remote refs are never read.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import JsonValue, ValidationError

from skillforge.knowledge.extractors.tool_base import (
    ToolExtractionResult,
    reference_within_budget,
    verified_reference,
)
from skillforge.knowledge.extractors.tool_base import (
    safe_metadata_value as _safe_value,
)
from skillforge.knowledge.normalize import NormalizedDocument
from skillforge.models.knowledge import (
    CapabilityDraft,
    CapabilityKind,
    ConfigKeySpec,
    KnowledgeSource,
    KnowledgeTarget,
    ToolProvenance,
)
from skillforge.security.redaction import is_sensitive_name, redact_text

_KEY = re.compile(r"^[A-Za-z_][\w-]*$")


class ConfigReferenceExtractor:
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
                "config reference rejected: source/target/version proof mismatch"
            )
            return result
        if max_capabilities < 1 or not 0 <= max_quote_chars <= 1000:
            raise ValueError("invalid config extraction budgets")
        if not reference_within_budget(document):
            result.diagnostics.append("config reference character budget exceeded")
            return result
        for index, section in enumerate(document.sections):
            match = re.fullmatch(
                r"(?:config|configuration)\s+(toml|ini|json|yaml|xml)\s+`?([\w.-]+)`?",
                section.heading,
                re.I,
            )
            if match:
                path = match[2].split(".")
                if len(path) > 20 or not all(_KEY.fullmatch(part) for part in path):
                    result.diagnostics.append(f"unsupported config key at section:{index}")
                    continue
                lines = section.text.splitlines()
                metadata: dict[str, str] = {}
                description: list[str] = []
                for line in lines:
                    field_match = re.fullmatch(
                        r"(Type|Default|Required|Choices):\s*(.*)", line, re.I
                    )
                    if field_match:
                        metadata[field_match[1].lower()] = field_match[2]
                    else:
                        description.append(line)
                default: JsonValue = None
                known = "default" in metadata
                if known:
                    raw = metadata["default"]
                    try:
                        default = _safe_value(
                            json.loads(raw), sensitive=is_sensitive_name(path[-1])
                        )
                    except (ValueError, ValidationError):
                        # Unquoted prose such as "platform dependent" is not a default fact.
                        known = False
                        result.diagnostics.append(f"unparsed default at section:{index}")
                required = {"true": True, "false": False}.get(metadata.get("required", "").lower())
                choices = None
                if "choices" in metadata:
                    try:
                        parsed = json.loads(metadata["choices"])
                        if isinstance(parsed, list):
                            choices = [
                                _safe_value(item, sensitive=is_sensitive_name(path[-1]))
                                for item in parsed
                            ]
                    except (ValueError, ValidationError):
                        result.diagnostics.append(f"unparsed choices at section:{index}")
                for length in range(1, len(path)):
                    prefix = path[:length]
                    qualified = "config:" + ".".join(prefix)
                    if not any(item.qualified_name == qualified for item in result.drafts):
                        result.drafts.append(
                            self._draft(
                                target,
                                source,
                                prefix,
                                ConfigKeySpec(key_path=prefix, file_format=match[1].lower()),
                                locator=f"section:{index}:path-namespace",
                                section=True,
                            )
                        )
                result.drafts.append(
                    self._draft(
                        target,
                        source,
                        path,
                        ConfigKeySpec(
                            key_path=path,
                            file_format=match[1].lower(),
                            value_type=metadata.get("type"),
                            default_known=known,
                            default=default,
                            required=required,
                            choices=choices,
                        ),
                        locator=f"section:{index}",
                        description=redact_text("\n".join(description))[:max_quote_chars],
                    )
                )
            elif section.heading.lower() in {"configuration schema", "config schema"}:
                for block in section.code_blocks:
                    try:
                        schema = json.loads(block)
                    except (ValueError, RecursionError):
                        result.diagnostics.append("invalid embedded config schema")
                        continue
                    remaining = max_capabilities - len(result.drafts)
                    if remaining < 1:
                        break
                    extracted = self.extract_schema(
                        schema,
                        source,
                        target,
                        max_capabilities=remaining,
                        max_quote_chars=max_quote_chars,
                    )
                    result.drafts.extend(extracted.drafts)
                    result.diagnostics.extend(extracted.diagnostics)
            if len(result.drafts) >= max_capabilities:
                result.drafts = result.drafts[:max_capabilities]
                result.diagnostics.append("config capability budget reached")
                break
        return result

    @staticmethod
    def _draft(
        target: KnowledgeTarget,
        source: KnowledgeSource,
        path: list[str],
        metadata: ConfigKeySpec,
        *,
        locator: str,
        description: str = "",
        section: bool = False,
    ) -> CapabilityDraft:
        return CapabilityDraft(
            technology=target.technology,
            ecosystem=target.ecosystem,
            version=target.version,
            kind=CapabilityKind.CONFIG_SECTION if section else CapabilityKind.CONFIG_KEY,
            name=path[-1],
            qualified_name="config:" + ".".join(path),
            parent_qualified="config:" + ".".join(path[:-1]) if len(path) > 1 else None,
            description=redact_text(description),
            config=metadata,
            sources=[source.id],
            tool_provenance=[
                ToolProvenance(
                    source_id=source.id,
                    locator=locator,
                    extractor="config-reference-v1",
                )
            ],
        )

    def extract_schema(
        self,
        schema: Any,
        source: KnowledgeSource,
        target: KnowledgeTarget,
        *,
        file_format: str | None = None,
        max_capabilities: int = 20_000,
        max_quote_chars: int = 200,
        max_depth: int = 20,
    ) -> ToolExtractionResult:
        result = ToolExtractionResult()
        if (
            not source.capability_eligible
            or source.ecosystem != target.ecosystem
            or source.technology.lower() != target.technology.lower()
            or source.version != target.version
        ):
            result.diagnostics.append("schema rejected: source/target/version proof mismatch")
            return result
        if max_capabilities < 1 or max_depth < 1 or not 0 <= max_quote_chars <= 1000:
            raise ValueError("invalid config schema budgets")
        try:
            if (
                not isinstance(schema, dict)
                or len(json.dumps(schema, allow_nan=False).encode()) > 2 * 1024 * 1024
            ):
                raise ValueError("schema must be a bounded JSON object")
        except (ValueError, TypeError, RecursionError):
            result.diagnostics.append("invalid or oversized schema")
            return result
        root = schema
        nodes = 0

        def resolve(
            node: dict[str, Any], active: frozenset[str]
        ) -> tuple[dict[str, Any] | None, frozenset[str]]:
            # Resolve only local RFC6901 pointers. Do not fetch or execute refs.
            while "$ref" in node:
                ref = node["$ref"]
                if not isinstance(ref, str) or not ref.startswith("#/"):
                    result.diagnostics.append("remote or unsupported schema ref skipped")
                    return None, active
                if ref in active or len(active) >= max_depth:
                    result.diagnostics.append("cyclic or over-depth schema ref skipped")
                    return None, active
                active = active | {ref}
                pointed: Any = root
                try:
                    for part in ref[2:].split("/"):
                        pointed = pointed[part.replace("~1", "/").replace("~0", "~")]
                except (KeyError, TypeError):
                    result.diagnostics.append("unresolved local schema ref skipped")
                    return None, active
                if not isinstance(pointed, dict):
                    result.diagnostics.append("non-object schema ref skipped")
                    return None, active
                if len(node) > 1:
                    result.diagnostics.append("schema ref siblings not merged")
                node = pointed
            return node, active

        def walk(
            node: dict[str, Any],
            path: list[str],
            pointer: str,
            active: frozenset[str],
            required: bool | None,
        ) -> None:
            nonlocal nodes
            nodes += 1
            if nodes > 4000 or len(path) > max_depth or len(result.drafts) >= max_capabilities:
                result.diagnostics.append("schema traversal/capability budget reached")
                return
            resolved_node, active = resolve(node, active)
            if resolved_node is None:
                return
            node = resolved_node
            if any(
                keyword in node for keyword in ("allOf", "anyOf", "oneOf", "if", "then", "else")
            ):
                result.diagnostics.append(f"complex schema branch not expanded at {pointer}")
            properties = node.get("properties")
            if path:
                type_value = node.get("type")
                type_name = type_value if isinstance(type_value, str) else None
                items = node.get("items")
                if (
                    type_name == "array"
                    and isinstance(items, dict)
                    and isinstance(items.get("type"), str)
                ):
                    type_name = f"array[{items['type']}]"
                sensitive = is_sensitive_name(path[-1])
                enum = node.get("enum")
                metadata = ConfigKeySpec(
                    key_path=path,
                    file_format=file_format,
                    value_type=type_name,
                    required=required,
                    default_known="default" in node,
                    default=_safe_value(node.get("default"), sensitive=sensitive)
                    if "default" in node
                    else None,
                    choices=[_safe_value(item, sensitive=sensitive) for item in enum]
                    if isinstance(enum, list)
                    else None,
                )
                description = node.get("description")
                result.drafts.append(
                    self._draft(
                        target,
                        source,
                        path,
                        metadata,
                        locator=pointer,
                        description=redact_text(description)[:max_quote_chars]
                        if isinstance(description, str)
                        else "",
                        section=isinstance(properties, dict),
                    )
                )
            if not isinstance(properties, dict):
                return
            required_names = node.get("required")
            for name, child in sorted(properties.items(), key=lambda item: str(item[0])):
                if (
                    not isinstance(name, str)
                    or not _KEY.fullmatch(name)
                    or not isinstance(child, dict)
                ):
                    result.diagnostics.append(f"unsupported property at {pointer}")
                    continue
                child_required: bool | None = (
                    name in required_names if isinstance(required_names, list) else False
                )
                if any(
                    keyword in node for keyword in ("allOf", "anyOf", "oneOf", "if", "then", "else")
                ):
                    child_required = None
                walk(child, [*path, name], pointer + "/properties/" + name, active, child_required)
                if len(result.drafts) >= max_capabilities or nodes > 4000:
                    result.diagnostics.append("schema traversal/capability budget reached")
                    break

        walk(schema, [], "#", frozenset(), None)
        result.diagnostics = sorted(set(result.diagnostics))
        return result
