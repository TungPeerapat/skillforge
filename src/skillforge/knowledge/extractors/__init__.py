"""Deterministic extraction and provenance-aware merging (K8).

Examples alone never define public API. Documentation contributes drafts only
from explicit typed API headings, backed by an accepted EXACT official source.
Artifact signatures take precedence; disagreements remain recorded.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Protocol

from skillforge.knowledge.capabilities import drafts_to_capabilities
from skillforge.knowledge.extractors.cli import CliReferenceExtractor
from skillforge.knowledge.extractors.config import ConfigReferenceExtractor
from skillforge.knowledge.extractors.tool_base import (
    ToolCapabilityExtractor,
    merge_tool_metadata,
    verified_reference,
)
from skillforge.knowledge.normalize import NormalizedDocument
from skillforge.models import (
    Capability,
    CapabilityDraft,
    CapabilityKind,
    Certainty,
    KnowledgeSource,
    KnowledgeTarget,
)
from skillforge.security.redaction import redact_text


@dataclass(frozen=True)
class ExtractionPlan:
    target: KnowledgeTarget
    max_capabilities: int = 20_000
    max_quote_chars: int = 200

    def __post_init__(self) -> None:
        if self.max_capabilities < 1 or not 0 <= self.max_quote_chars <= 1000:
            raise ValueError("invalid extraction budgets")


@dataclass
class ExtractionResult:
    capabilities: list[Capability] = field(default_factory=list)
    conflicts: list[dict[str, str]] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)


class CapabilityExtractor(Protocol):
    def extract(
        self,
        document: NormalizedDocument,
        source: KnowledgeSource,
        plan: ExtractionPlan,
    ) -> list[CapabilityDraft]: ...


class DocumentationExtractor:
    # Require the author to identify the declaration's kind; ordinary headings
    # and example code are not proof that a symbol is public API.
    _HEADING = re.compile(
        r"^(function|method|class|interface|enum|type|constant|property|module|namespace)"
        r"\s+`?([A-Za-z_$][\w.$:]*)`?(?:\s*\(.*\))?\s*$",
        re.I,
    )

    def extract(
        self,
        document: NormalizedDocument,
        source: KnowledgeSource,
        plan: ExtractionPlan,
    ) -> list[CapabilityDraft]:
        if not verified_reference(document, source, plan.target):
            return []
        drafts: list[CapabilityDraft] = []
        for section in document.sections:
            match = self._HEADING.fullmatch(section.heading)
            if match is None:
                continue
            kind = CapabilityKind(match[1].lower())
            qualified = match[2]
            drafts.append(
                CapabilityDraft(
                    technology=plan.target.technology,
                    ecosystem=plan.target.ecosystem,
                    version=plan.target.version,
                    kind=kind,
                    kind_detail="explicit official API heading",
                    name=qualified.rsplit(".", 1)[-1],
                    qualified_name=qualified,
                    description=redact_text(section.text)[: plan.max_quote_chars],
                    parent_qualified=qualified.rpartition(".")[0] or None,
                    confidence=Certainty.INFERENCE,
                    sources=[source.id],
                )
            )
            if len(drafts) >= plan.max_capabilities:
                break
        return drafts


def extract_capabilities(
    plan: ExtractionPlan,
    *,
    artifacts: Iterable[CapabilityDraft] = (),
    documents: Iterable[NormalizedDocument] = (),
    sources: Mapping[str, KnowledgeSource] | None = None,
) -> ExtractionResult:
    """Artifact facts first, then official documents; no LLM authority."""
    result = ExtractionResult()
    merged: dict[tuple[str, str], CapabilityDraft] = {}
    source_map = sources or {}

    def merge(draft: CapabilityDraft) -> None:
        if (
            draft.technology.lower() != plan.target.technology.lower()
            or draft.ecosystem != plan.target.ecosystem
            or draft.version != plan.target.version
        ):
            result.diagnostics.append("draft target/version mismatch; skipped")
            return
        trusted_sources = [
            identifier
            for identifier in draft.sources
            if identifier in source_map
            and source_map[identifier].capability_eligible
            and source_map[identifier].technology.lower() == plan.target.technology.lower()
            and source_map[identifier].version == plan.target.version
        ]
        if not draft.evidence and not trusted_sources:
            result.diagnostics.append(f"unproven draft skipped: {draft.qualified_name}")
            return
        draft = draft.model_copy(deep=True)
        draft.sources = trusted_sources
        draft.description = redact_text(draft.description)[: plan.max_quote_chars]
        key = (draft.qualified_name, draft.kind.value)
        previous = merged.get(key)
        if previous is None:
            if len(merged) < plan.max_capabilities:
                merged[key] = draft
            else:
                result.diagnostics.append("capability budget reached; extra drafts skipped")
            return
        if previous.signature and draft.signature and previous.signature != draft.signature:
            result.conflicts.append(
                {
                    "symbol": draft.qualified_name,
                    "kind": "signature",
                    "chosen": "first authoritative draft",
                }
            )
        if previous.signature is None:
            previous.signature = draft.signature
        for metadata_field in ("cli", "option", "argument", "config"):
            before, after = getattr(previous, metadata_field), getattr(draft, metadata_field)
            if before is None:
                setattr(previous, metadata_field, after)
            elif after is not None:
                combined, contradictions = merge_tool_metadata(before, after)
                setattr(previous, metadata_field, combined)
                if contradictions:
                    result.conflicts.append(
                        {
                            "symbol": draft.qualified_name,
                            "kind": metadata_field,
                            "chosen": "first authoritative draft",
                            "fields": ",".join(contradictions),
                        }
                    )
        previous.tool_provenance.extend(
            item for item in draft.tool_provenance if item not in previous.tool_provenance
        )
        if not previous.description:
            previous.description = draft.description
        previous.sources = sorted(set(previous.sources) | set(draft.sources))
        previous.evidence.extend(item for item in draft.evidence if item not in previous.evidence)

    for draft in sorted(
        artifacts,
        key=lambda item: (
            0 if item.confidence is Certainty.FACT else 1,
            item.qualified_name,
            item.kind.value,
        ),
    ):
        merge(draft)
    extractor = DocumentationExtractor()
    for document in sorted(documents, key=lambda item: item.source_id):
        source = source_map.get(document.source_id)
        if source is None:
            result.diagnostics.append("document with unknown source skipped")
            continue
        for draft in extractor.extract(document, source, plan):
            merge(draft)
        tool_extractors: list[ToolCapabilityExtractor] = [ConfigReferenceExtractor()]
        if plan.target.technology.lower() in CliReferenceExtractor.supported_technologies:
            tool_extractors.append(CliReferenceExtractor())
        for tool_extractor in tool_extractors:
            extracted = tool_extractor.extract_reference(
                document,
                source,
                plan.target,
                max_capabilities=plan.max_capabilities,
                max_quote_chars=plan.max_quote_chars,
            )
            for draft in extracted.drafts:
                merge(draft)
            result.diagnostics.extend(extracted.diagnostics)
    result.capabilities = drafts_to_capabilities(merged.values())
    result.diagnostics = sorted(set(result.diagnostics))
    return result
