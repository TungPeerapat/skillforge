"""Knowledge-ref staleness checks (K4).

A skill manifest may point at one global-store entry via
:class:`~skillforge.models.skill.KnowledgeRef`. This module answers whether that
reference is still fresh, missing, hash-mismatched, or pointing at a corrupt
entry — deterministically and without touching the network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from skillforge.knowledge.store import KnowledgeStore
from skillforge.models import KnowledgeRef, KnowledgeRegistryEntry, KnowledgeTarget


class KnowledgeStalenessStatus(StrEnum):
    FRESH = "fresh"
    MISSING = "missing"
    HASH_MISMATCH = "hash_mismatch"
    CORRUPT = "corrupt"


@dataclass
class KnowledgeStalenessReport:
    status: KnowledgeStalenessStatus
    reasons: list[str] = field(default_factory=list)
    entry: KnowledgeRegistryEntry | None = None

    @property
    def is_fresh(self) -> bool:
        return self.status is KnowledgeStalenessStatus.FRESH


def check_knowledge_ref(ref: KnowledgeRef, store: KnowledgeStore) -> KnowledgeStalenessReport:
    """Compare a stored ``KnowledgeRef`` against the live store registry."""
    entry = store.find(ref.technology, ref.version)
    if entry is None:
        return KnowledgeStalenessReport(
            KnowledgeStalenessStatus.MISSING,
            [
                f"{ref.technology} {ref.version} is not present in the knowledge store "
                f"({store.home})"
            ],
        )
    reasons: list[str] = []
    if ref.store_hash and ref.store_hash != entry.store_hash:
        return KnowledgeStalenessReport(
            KnowledgeStalenessStatus.HASH_MISMATCH,
            [
                f"stored reference hash {ref.store_hash} does not match "
                f"the current entry hash {entry.store_hash}"
            ],
            entry,
        )
    if not ref.store_hash:
        reasons.append("reference has no store hash; matched by technology/version only")
    validation = store.validate_entry(
        KnowledgeTarget(
            ecosystem=entry.ecosystem,
            technology=entry.technology,
            version=entry.version,
        )
    )
    if not validation.ok:
        return KnowledgeStalenessReport(
            KnowledgeStalenessStatus.CORRUPT,
            list(validation.issues),
            entry,
        )
    return KnowledgeStalenessReport(KnowledgeStalenessStatus.FRESH, reasons, entry)


__all__ = [
    "KnowledgeStalenessReport",
    "KnowledgeStalenessStatus",
    "check_knowledge_ref",
]
