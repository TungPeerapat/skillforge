"""Shared value objects: certainty, evidence, severity, risk level."""

from __future__ import annotations

from enum import IntEnum, StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from skillforge.utils.text import truncate

SNIPPET_LIMIT = 400


class Certainty(StrEnum):
    """How much trust a statement deserves.

    SkillForge never renders an ``INFERENCE`` or ``UNKNOWN`` as a fact.
    """

    FACT = "fact"
    INFERENCE = "inference"
    UNKNOWN = "unknown"


class RiskLevel(IntEnum):
    """Ordered command-risk levels (higher is riskier)."""

    SAFE = 1
    REVIEW = 2
    DANGEROUS = 3

    @property
    def label(self) -> str:
        return {
            RiskLevel.SAFE: "SAFE",
            RiskLevel.REVIEW: "REVIEW_REQUIRED",
            RiskLevel.DANGEROUS: "DANGEROUS",
        }[self]

    @classmethod
    def from_config(cls, value: str) -> RiskLevel:
        mapping = {"safe": cls.SAFE, "review": cls.REVIEW, "dangerous": cls.DANGEROUS}
        return mapping.get(value.strip().lower(), cls.REVIEW)

    @property
    def is_safe(self) -> bool:
        return self is RiskLevel.SAFE

    @property
    def is_dangerous(self) -> bool:
        return self is RiskLevel.DANGEROUS

    @property
    def needs_review(self) -> bool:
        return self is RiskLevel.REVIEW


class Severity(StrEnum):
    """Severity for validation findings and repository risks."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def is_failure(self) -> bool:
        return self in (Severity.HIGH, Severity.CRITICAL)


class Evidence(BaseModel):
    """A single observation that supports (or contradicts) a claim.

    ``snippet`` is redacted automatically on construction, so secrets cannot
    travel through the domain model even if a detector forgets to redact.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Certainty = Certainty.FACT
    source: str = Field(description="Repository-relative path, or a marker like '<git>'")
    locator: str = ""
    detail: str = ""
    snippet: str = ""
    weight: float = Field(default=0.6, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _redact_snippet(self) -> Self:
        if not self.snippet:
            return self
        # Imported lazily to keep the models package free of import cycles.
        from skillforge.security.redaction import redact_text

        object.__setattr__(self, "snippet", truncate(redact_text(self.snippet), SNIPPET_LIMIT))
        return self

    @property
    def label(self) -> str:
        """Human-readable provenance label, e.g. ``package.json:scripts.dev``."""
        if self.locator:
            return f"{self.source}:{self.locator}"
        return self.source

    def describe(self) -> str:
        parts = [self.label]
        if self.detail:
            parts.append(self.detail)
        return " — ".join(parts)


def confidence_from_evidence(
    evidence: list[Evidence], *, base: float = 0.4, cap: float = 0.99
) -> float:
    """Derive a confidence score from evidence weights.

    A single strong observation is not certainty; several independent
    observations raise confidence. The result never reaches 1.0.
    """
    if not evidence:
        return 0.0
    strongest = max(item.weight for item in evidence)
    bonus = min(0.15, 0.05 * (len(evidence) - 1))
    return round(min(cap, max(base, strongest) + bonus), 3)


class Unknown(BaseModel):
    """Something analysis could not determine, and how to resolve it."""

    model_config = ConfigDict(extra="forbid")

    id: str
    question: str
    why: str = ""
    suggested_check: str = ""
    evidence: list[Evidence] = Field(default_factory=list)
