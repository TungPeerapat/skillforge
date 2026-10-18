"""Capability IR normalization (K5).

Drafts (from fixtures at this stage; real extraction lands in K6/K8) are
normalized into deterministic :class:`~skillforge.models.Capability` records:
stable ids, sorted and deduplicated lists, domains detected when missing,
ready to be indexed and stored.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from skillforge.knowledge.taxonomy import detect_domains
from skillforge.models import (
    Capability,
    CapabilityDraft,
    CapabilityRelationship,
)
from skillforge.models.knowledge import CapabilitySignature


def _sorted_unique(values: Iterable[str]) -> list[str]:
    return sorted({value for value in values if value})


def normalize_capability(capability: Capability) -> Capability:
    """Return a normalized copy; never mutates the input."""
    qualified_name = capability.qualified_name.strip() or capability.name.strip()
    name = capability.name.strip() or qualified_name.rpartition(".")[2]
    domains = _sorted_unique(capability.domains)
    if not domains:
        domains = detect_domains(
            name,
            qualified_name,
            capability.kind,
            extra_text=capability.description,
        )
    relationships = _normalize_relationships(capability.relationships)
    signature = _normalize_signature(capability.signature)
    capability_id = capability.id.strip() or Capability.make_id(
        capability.ecosystem,
        capability.technology,
        capability.version,
        qualified_name,
        capability.kind,
    )
    return capability.model_copy(
        update={
            "id": capability_id,
            "name": name,
            "qualified_name": qualified_name,
            "domains": domains,
            "relationships": relationships,
            "signature": signature,
            "sources": _sorted_unique(capability.sources),
        }
    )


def _normalize_relationships(
    relationships: Sequence[CapabilityRelationship],
) -> list[CapabilityRelationship]:
    unique = {(item.kind.value, item.target_id, item.detail): item for item in relationships}
    return [unique[key] for key in sorted(unique, key=lambda key: (key[0], key[1], key[2]))]


def _normalize_signature(signature: CapabilitySignature | None) -> CapabilitySignature | None:
    if signature is None:
        return None
    return signature.model_copy(
        update={
            "raises": _sorted_unique(signature.raises),
            "parameters": list(signature.parameters),
        }
    )


def normalize_capabilities(capabilities: Iterable[Capability]) -> list[Capability]:
    """Normalize, deduplicate by id (first occurrence wins), and sort stably."""
    seen: dict[str, Capability] = {}
    for capability in capabilities:
        normalized = normalize_capability(capability)
        seen.setdefault(normalized.id, normalized)
    return sorted(
        seen.values(),
        key=lambda item: (item.qualified_name, item.kind.rank, item.id),
    )


def capability_kind_counts(capabilities: Iterable[Capability]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for capability in capabilities:
        key = capability.kind.value
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def capability_domain_counts(capabilities: Iterable[Capability]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for capability in capabilities:
        for domain in capability.domains:
            counts[domain] = counts.get(domain, 0) + 1
    return dict(sorted(counts.items()))


def drafts_to_capabilities(drafts: Iterable[CapabilityDraft]) -> list[Capability]:
    """Normalize introspection drafts into the capability IR (K6 → K5)."""
    ordered = sorted(drafts, key=lambda item: (item.qualified_name, item.kind.rank, item.name))
    id_by_qualified: dict[str, str] = {}
    for draft in ordered:
        capability_id = Capability.make_id(
            draft.ecosystem, draft.technology, draft.version, draft.qualified_name, draft.kind
        )
        id_by_qualified.setdefault(draft.qualified_name, capability_id)
    capabilities: list[Capability] = []
    for draft in ordered:
        capabilities.append(
            Capability(
                id=Capability.make_id(
                    draft.ecosystem,
                    draft.technology,
                    draft.version,
                    draft.qualified_name,
                    draft.kind,
                ),
                technology=draft.technology,
                ecosystem=draft.ecosystem,
                version=draft.version,
                kind=draft.kind,
                kind_detail=draft.kind_detail,
                name=draft.name,
                qualified_name=draft.qualified_name,
                signature=draft.signature,
                description=draft.description,
                parent_id=(
                    id_by_qualified.get(draft.parent_qualified) if draft.parent_qualified else None
                ),
                deprecated=draft.deprecated,
                experimental=draft.experimental,
                sources=list(draft.sources),
                evidence=list(draft.evidence),
                confidence=draft.confidence,
                cli=draft.cli,
                option=draft.option,
                argument=draft.argument,
                config=draft.config,
                tool_provenance=list(draft.tool_provenance),
            )
        )
    return normalize_capabilities(capabilities)


__all__ = [
    "capability_domain_counts",
    "capability_kind_counts",
    "drafts_to_capabilities",
    "normalize_capabilities",
    "normalize_capability",
]
