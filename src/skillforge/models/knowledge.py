"""Versioned library intelligence models (pure data, no I/O).

v0.3 knows two separate views of a project's technology surface:

* the **dependency inventory** — what the project declares and (later) what
  lockfiles/installed artifacts resolve, with role, scope, and importance;
* the **versioned capability knowledge** — what an exact library version
  exposes, learned from authoritative sources (later milestones).

Nothing here executes anything or touches the network.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from skillforge.models.common import Certainty, Evidence
from skillforge.models.repository import DependencyScope


class DependencyRelationship(StrEnum):
    """How an entry relates to the project."""

    DIRECT = "direct"
    TRANSITIVE = "transitive"
    WORKSPACE = "workspace"
    UNKNOWN = "unknown"


class DependencyRole(StrEnum):
    """What a dependency or technology is for."""

    FRAMEWORK = "framework"
    LIBRARY = "library"
    RUNTIME = "runtime"
    SDK = "sdk"
    UI_COMPONENT_LIBRARY = "ui_component_library"
    DATABASE = "database"
    ORM = "orm"
    MIGRATION_TOOL = "migration_tool"
    BUILD_TOOL = "build_tool"
    TEST_TOOL = "test_tool"
    LINTER = "linter"
    FORMATTER = "formatter"
    CONTAINER_TOOL = "container_tool"
    INFRA_TOOL = "infra_tool"
    CLI_TOOL = "cli_tool"
    CODE_GENERATOR = "code_generator"
    OBSERVABILITY = "observability"
    OTHER = "other"


class KnowledgePriority(StrEnum):
    """How much knowledge effort an entry deserves."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    TRANSITIVE = "transitive"

    @property
    def rank(self) -> int:
        return {
            KnowledgePriority.CRITICAL: 0,
            KnowledgePriority.HIGH: 1,
            KnowledgePriority.MEDIUM: 2,
            KnowledgePriority.LOW: 3,
            KnowledgePriority.TRANSITIVE: 4,
        }[self]

    @classmethod
    def from_config(cls, value: str) -> KnowledgePriority | None:
        try:
            return cls(value.strip().lower())
        except ValueError:
            return None


class ResolutionMethod(StrEnum):
    """How an exact version was established (ladder in the v0.3 plan)."""

    LOCKFILE = "lockfile"
    INSTALLED_ARTIFACT = "installed_artifact"
    MANIFEST_PIN = "manifest_pin"
    MANIFEST_RANGE = "manifest_range"
    NONE = "none"

    @property
    def is_exact(self) -> bool:
        return self in (
            ResolutionMethod.LOCKFILE,
            ResolutionMethod.INSTALLED_ARTIFACT,
            ResolutionMethod.MANIFEST_PIN,
        )


class KnowledgeTarget(BaseModel):
    """A technology/version worth learning knowledge for."""

    model_config = ConfigDict(extra="forbid")

    ecosystem: str
    technology: str
    version: str

    @property
    def key(self) -> str:
        return f"{self.ecosystem}:{self.technology.lower()}@{self.version}"


class VersionInfo(BaseModel):
    """Declared vs resolved version, with the evidence that resolved it."""

    model_config = ConfigDict(extra="forbid")

    declared: str | None = None
    resolved: str | None = None
    method: ResolutionMethod = ResolutionMethod.NONE
    resolved_from: str | None = None
    certainty: Certainty = Certainty.UNKNOWN
    notes: list[str] = Field(default_factory=list)

    @property
    def is_resolved(self) -> bool:
        return bool(self.resolved) and self.method.is_exact


class VersionClaim(BaseModel):
    """One source's claim about an exact version (kept even when conflicting)."""

    model_config = ConfigDict(extra="forbid")

    version: str
    method: ResolutionMethod
    source: str
    detail: str = ""


class KnowledgeConflict(BaseModel):
    """Sources disagreed; both claims are kept and the choice is recorded."""

    model_config = ConfigDict(extra="forbid")

    ecosystem: str
    name: str
    claims: list[VersionClaim] = Field(default_factory=list)
    chosen: str | None = None
    reason: str = ""

    @property
    def versions(self) -> list[str]:
        return sorted({claim.version for claim in self.claims})


class SourceAuthority(StrEnum):
    """How authoritative a knowledge source is (lower rank = stronger)."""

    EXACT_ARTIFACT = "exact_artifact"
    EXACT_API_REFERENCE = "exact_api_reference"
    EXACT_DOCS = "exact_docs"
    OFFICIAL_TYPE_DECLARATIONS = "official_type_declarations"
    OFFICIAL_PACKAGE_METADATA = "official_package_metadata"
    OFFICIAL_REPOSITORY_TAG = "official_repository_tag"
    OFFICIAL_GENERATED_REFERENCE = "official_generated_reference"
    OFFICIAL_CHANGELOG = "official_changelog"
    OFFICIAL_MIGRATION_GUIDE = "official_migration_guide"
    OFFICIAL_EXAMPLES = "official_examples"
    THIRD_PARTY = "third_party"
    UNKNOWN = "unknown"

    @property
    def rank(self) -> int:
        return {
            SourceAuthority.EXACT_ARTIFACT: 0,
            SourceAuthority.EXACT_API_REFERENCE: 1,
            SourceAuthority.EXACT_DOCS: 2,
            SourceAuthority.OFFICIAL_TYPE_DECLARATIONS: 3,
            SourceAuthority.OFFICIAL_PACKAGE_METADATA: 4,
            SourceAuthority.OFFICIAL_REPOSITORY_TAG: 5,
            SourceAuthority.OFFICIAL_GENERATED_REFERENCE: 6,
            SourceAuthority.OFFICIAL_CHANGELOG: 7,
            SourceAuthority.OFFICIAL_MIGRATION_GUIDE: 8,
            SourceAuthority.OFFICIAL_EXAMPLES: 9,
            SourceAuthority.THIRD_PARTY: 10,
            SourceAuthority.UNKNOWN: 11,
        }[self]

    @property
    def is_official(self) -> bool:
        return self not in (SourceAuthority.THIRD_PARTY, SourceAuthority.UNKNOWN)


class VersionMatchKind(StrEnum):
    """How well a source URL matches the resolved version."""

    EXACT = "exact"
    COMPATIBLE = "compatible"
    MAJOR_MATCH = "major_match"
    LATEST_ONLY = "latest_only"
    UNKNOWN = "unknown"
    MISMATCH = "mismatch"

    @property
    def rank(self) -> int:
        return {
            VersionMatchKind.EXACT: 0,
            VersionMatchKind.COMPATIBLE: 1,
            VersionMatchKind.MAJOR_MATCH: 2,
            VersionMatchKind.LATEST_ONLY: 3,
            VersionMatchKind.UNKNOWN: 4,
            VersionMatchKind.MISMATCH: 5,
        }[self]

    @property
    def is_exact(self) -> bool:
        return self is VersionMatchKind.EXACT

    @property
    def contributes_to_capabilities(self) -> bool:
        """Only exact-version sources may back capability claims (v0.3 rule)."""
        return self is VersionMatchKind.EXACT


class SourceVerificationKind(StrEnum):
    """Evidence for accepting a source — or the reason it was rejected."""

    REGISTRY_METADATA_MATCH = "registry_metadata_match"
    DECLARED_HOMEPAGE = "declared_homepage"
    DECLARED_REPOSITORY = "declared_repository"
    DECLARED_DOCUMENTATION = "declared_documentation"
    REGISTRY_DOMAIN_MAP = "registry_domain_map"
    RELEASE_TAG_MATCH = "release_tag_match"
    ARTIFACT_EMBEDDED = "artifact_embedded"
    USER_CONFIGURED = "user_configured"
    REJECTED_UNKNOWN_HOST = "rejected_unknown_host"
    REJECTED_VERSION_MISMATCH = "rejected_version_mismatch"
    REJECTED_COMMUNITY = "rejected_community"
    REJECTED_CONTENT_TYPE = "rejected_content_type"
    REJECTED_ROBOTS = "rejected_robots"
    #: Additive K3 guard: HTTPS is mandatory for every source.
    REJECTED_INSECURE_SCHEME = "rejected_insecure_scheme"

    @property
    def is_rejection(self) -> bool:
        return self.value.startswith("rejected_")


class SourceVerification(BaseModel):
    """One piece of evidence about a candidate source."""

    model_config = ConfigDict(extra="forbid")

    kind: SourceVerificationKind
    detail: str = ""
    evidence: list[Evidence] = Field(default_factory=list)


class KnowledgeSource(BaseModel):
    """A candidate documentation/source URL with authority and match evidence."""

    model_config = ConfigDict(extra="forbid")

    id: str
    technology: str
    version: str
    url: str
    title: str = ""
    authority: SourceAuthority = SourceAuthority.UNKNOWN
    verification: list[SourceVerification] = Field(default_factory=list)
    version_match: VersionMatchKind = VersionMatchKind.UNKNOWN
    content_hash: str | None = None
    license: str | None = None
    retrieved_at: datetime | None = None
    notes: list[str] = Field(default_factory=list)
    accepted: bool = True

    @property
    def rejections(self) -> list[SourceVerification]:
        return [item for item in self.verification if item.kind.is_rejection]

    @property
    def rejection_reason(self) -> str:
        for item in self.rejections:
            if item.detail:
                return item.detail
        return ""

    @property
    def capability_eligible(self) -> bool:
        """Accepted AND exact-version matched; MISMATCH/LATEST_ONLY never qualify."""
        return (
            self.accepted
            and self.authority.is_official
            and bool(self.verification)
            and not self.rejections
            and self.version_match.contributes_to_capabilities
        )

    @staticmethod
    def make_id(technology: str, version: str, url: str) -> str:
        payload = f"{technology.lower()}@{version}:{url}"
        return "src-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


class CapabilityKind(StrEnum):
    """What kind of public capability a record describes (K5)."""

    PACKAGE = "package"
    MODULE = "module"
    NAMESPACE = "namespace"
    CLASS = "class"
    INTERFACE = "interface"
    ENUM = "enum"
    FUNCTION = "function"
    METHOD = "method"
    PROPERTY = "property"
    CONSTANT = "constant"
    TYPE = "type"
    EXCEPTION = "exception"
    DECORATOR = "decorator"
    CLI_COMMAND = "cli_command"
    CONFIG_OPTION = "config_option"
    EVENT = "event"
    HOOK = "hook"
    MIDDLEWARE = "middleware"

    @property
    def rank(self) -> int:
        """Deterministic tie-break order (declaration order)."""
        return list(type(self)).index(self)


class CapabilityParameter(BaseModel):
    """One parameter of a callable capability."""

    model_config = ConfigDict(extra="forbid")

    name: str
    type: str | None = None
    default: str | None = None
    required: bool = True
    kind: str = ""


class CapabilitySignature(BaseModel):
    """Structured call signature."""

    model_config = ConfigDict(extra="forbid")

    parameters: list[CapabilityParameter] = Field(default_factory=list)
    returns: str | None = None
    raises: list[str] = Field(default_factory=list)
    async_: bool = False
    static: bool = False
    kind: str = ""


class CapabilityRelationshipKind(StrEnum):
    MEMBER_OF = "member_of"
    RELATED_TO = "related_to"
    REPLACES = "replaces"
    EXTENDS = "extends"
    REQUIRES = "requires"
    SEE_ALSO = "see_also"
    DEPRECATED_BY = "deprecated_by"


class CapabilityRelationship(BaseModel):
    """Typed edge to another capability (graph edges land in K9)."""

    model_config = ConfigDict(extra="forbid")

    kind: CapabilityRelationshipKind
    target_id: str
    detail: str = ""


class VersionLifecycle(BaseModel):
    """Version boundaries for a capability, always evidence-backed or null."""

    model_config = ConfigDict(extra="forbid")

    introduced_in: str | None = None
    deprecated_in: str | None = None
    removed_in: str | None = None
    replacement: str | None = None
    status: str = ""
    evidence: list[str] = Field(default_factory=list)


class CapabilityDomain(BaseModel):
    """Curated taxonomy entry (for example ``http`` or ``serialization``)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str = ""
    keywords: tuple[str, ...] = ()


class ArtifactEvidence(BaseModel):
    """Where a draft was found inside an installed artifact.

    ``path`` is **package-relative** (for example ``fastapi/routing.py``);
    absolute machine paths never enter models, the store, or compiled skills.
    ``hash`` is the sha256 of the source file when available.
    """

    model_config = ConfigDict(extra="forbid")

    kind: str
    path: str = ""
    locator: str = ""
    hash: str = ""
    detail: str = ""


class CapabilityDraft(BaseModel):
    """Unnormalized capability found by an artifact introspector (K6)."""

    model_config = ConfigDict(extra="forbid")

    technology: str
    ecosystem: str
    version: str
    kind: CapabilityKind
    kind_detail: str = ""
    name: str
    qualified_name: str
    signature: CapabilitySignature | None = None
    description: str = ""
    parent_qualified: str | None = None
    deprecated: bool | None = None
    experimental: bool | None = None
    confidence: Certainty = Certainty.INFERENCE
    evidence: list[ArtifactEvidence] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)


class Capability(BaseModel):
    """One normalized public capability of an exact technology version."""

    model_config = ConfigDict(extra="forbid")

    id: str
    technology: str
    ecosystem: str
    version: str
    kind: CapabilityKind
    kind_detail: str = ""
    name: str
    qualified_name: str
    signature: CapabilitySignature | None = None
    description: str = ""
    parent_id: str | None = None
    relationships: list[CapabilityRelationship] = Field(default_factory=list)
    lifecycle: VersionLifecycle | None = None
    deprecated: bool | None = None
    experimental: bool | None = None
    domains: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    evidence: list[ArtifactEvidence] = Field(default_factory=list)
    confidence: Certainty = Certainty.INFERENCE

    @staticmethod
    def make_id(
        ecosystem: str, technology: str, version: str, qualified_name: str, kind: CapabilityKind
    ) -> str:
        payload = f"{ecosystem}:{technology.lower()}@{version}:{kind.value}:{qualified_name}"
        return "cap-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


class KnowledgeManifest(BaseModel):
    """Manifest for one stored (ecosystem, technology, version) entry (K4)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    technology: str
    ecosystem: str
    resolved_version: str
    registry_url: str | None = None
    #: Machine-local artifact identity/hash only; never exported into skills.
    artifact_identity: str = ""
    artifact_hash: str = ""
    sources: list[str] = Field(default_factory=list)
    #: relative file path -> sha256 of the file content as stored.
    source_hashes: dict[str, str] = Field(default_factory=dict)
    capability_count: int = 0
    capability_kinds: dict[str, int] = Field(default_factory=dict)
    domain_count: int = 0
    coverage: dict[str, int] = Field(default_factory=dict)
    version_match: VersionMatchKind = VersionMatchKind.UNKNOWN
    validation: str = "unvalidated"
    conflicts: int = 0
    cache_keys: list[str] = Field(default_factory=list)
    generated_at: datetime
    tool_version: str = ""
    warnings: list[str] = Field(default_factory=list)

    def content_hash(self) -> str:
        """Hash of the knowledge content (timestamps excluded → stable reuse)."""
        payload = json.dumps(
            self.model_dump(mode="json", exclude={"generated_at"}),
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @property
    def store_hash(self) -> str:
        return "kh-" + self.content_hash()[:16]


class KnowledgeRegistryEntry(BaseModel):
    """One tech/version present in the global store."""

    model_config = ConfigDict(extra="forbid")

    key: str
    ecosystem: str
    technology: str
    version: str
    store_hash: str
    manifest_hash: str
    capability_count: int = 0
    updated_at: datetime


class KnowledgeRegistry(BaseModel):
    """``registry.json``: tech/versions present plus manifest hashes."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    generated_at: datetime
    entries: list[KnowledgeRegistryEntry] = Field(default_factory=list)

    def by_key(self, key: str) -> KnowledgeRegistryEntry | None:
        for entry in self.entries:
            if entry.key == key:
                return entry
        return None

    def find(self, technology: str, version: str) -> KnowledgeRegistryEntry | None:
        wanted = technology.lower()
        for entry in self.entries:
            if entry.technology.lower() == wanted and entry.version == version:
                return entry
        return None


class InventoryEntry(BaseModel):
    """One dependency or tool the project uses."""

    model_config = ConfigDict(extra="forbid")

    id: str
    ecosystem: str
    name: str
    component: str | None = None
    relationship: DependencyRelationship = DependencyRelationship.UNKNOWN
    role: DependencyRole = DependencyRole.LIBRARY
    scope: DependencyScope = DependencyScope.UNKNOWN
    importance: KnowledgePriority = KnowledgePriority.MEDIUM
    version: VersionInfo = Field(default_factory=VersionInfo)
    manifests: list[str] = Field(default_factory=list)
    lockfiles: list[str] = Field(default_factory=list)
    transitive_of: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @property
    def key(self) -> str:
        suffix = f":{self.component}" if self.component else ""
        return f"{self.ecosystem}:{self.name.lower()}{suffix}"

    @property
    def provenance(self) -> str:
        return self.evidence[0].label if self.evidence else "repository"

    @staticmethod
    def make_id(ecosystem: str, name: str, component: str | None = None) -> str:
        payload = f"{ecosystem}:{name.lower()}:{component or ''}"
        return "dep-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


class DependencyInventory(BaseModel):
    """The complete, classified dependency surface of one repository."""

    model_config = ConfigDict(extra="forbid")

    repository: str
    generated_at: datetime
    entries: list[InventoryEntry] = Field(default_factory=list)
    knowledge_targets: list[KnowledgeTarget] = Field(default_factory=list)
    truncated: bool = False
    warnings: list[str] = Field(default_factory=list)

    def by_name(self, name: str) -> InventoryEntry | None:
        lowered = name.lower()
        for entry in self.entries:
            if entry.name.lower() == lowered:
                return entry
        return None

    def by_importance(self, priority: KnowledgePriority) -> list[InventoryEntry]:
        return [entry for entry in self.entries if entry.importance is priority]

    @property
    def direct(self) -> list[InventoryEntry]:
        return [
            entry for entry in self.entries if entry.relationship is DependencyRelationship.DIRECT
        ]

    @property
    def resolved_targets(self) -> list[KnowledgeTarget]:
        return self.knowledge_targets

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self.entries:
            counts[entry.importance.value] = counts.get(entry.importance.value, 0) + 1
        return dict(sorted(counts.items()))


__all__ = [
    "ArtifactEvidence",
    "Capability",
    "CapabilityDomain",
    "CapabilityDraft",
    "CapabilityKind",
    "CapabilityParameter",
    "CapabilityRelationship",
    "CapabilityRelationshipKind",
    "CapabilitySignature",
    "DependencyInventory",
    "DependencyRelationship",
    "DependencyRole",
    "InventoryEntry",
    "KnowledgeConflict",
    "KnowledgeManifest",
    "KnowledgePriority",
    "KnowledgeRegistry",
    "KnowledgeRegistryEntry",
    "KnowledgeSource",
    "KnowledgeTarget",
    "ResolutionMethod",
    "SourceAuthority",
    "SourceVerification",
    "SourceVerificationKind",
    "VersionClaim",
    "VersionInfo",
    "VersionLifecycle",
    "VersionMatchKind",
]
