"""Versioned library intelligence (v0.3).

K1 ships the deep dependency inventory: declared dependencies, detected
technologies, and used tooling are classified into one inventory with
relationship, scope, role, importance, and knowledge targets. K2 adds exact
version resolution from lockfiles and installed artifacts (never guessed).
Later milestones add official sources, artifact introspection, the capability
graph, and knowledge-skill compilation.
"""

from __future__ import annotations

from skillforge.knowledge.artifacts import (
    InstalledArtifact,
    InstalledIndex,
    collect_installed,
)
from skillforge.knowledge.authority import (
    COMMUNITY_HOSTS,
    AuthorityEntry,
    AuthorityMap,
    CandidateOrigin,
    OfficialSourcePolicy,
    SourceCandidate,
    default_authority_map,
    host_matches,
    match_version_in_url,
)
from skillforge.knowledge.capabilities import (
    capability_domain_counts,
    capability_kind_counts,
    drafts_to_capabilities,
    normalize_capabilities,
    normalize_capability,
)
from skillforge.knowledge.index import (
    KnowledgeIndex,
    build_index_payloads,
    write_indexes,
)
from skillforge.knowledge.introspect import (
    ArtifactLocation,
    Budgets,
    IntrospectionResult,
    introspect_artifact,
    introspector_for,
    locate_artifact,
)
from skillforge.knowledge.inventory import (
    PACKAGE_ECOSYSTEMS,
    build_inventory,
    classify_role,
)
from skillforge.knowledge.lockfiles import (
    LockedEntry,
    ParsedLockfile,
    parse_lockfile,
    parse_scan_lockfiles,
    parser_for,
)
from skillforge.knowledge.resolution import (
    ResolutionResult,
    resolve_inventory,
    resolve_repository,
)
from skillforge.knowledge.sources import (
    RegistryMetadata,
    SourceResolver,
    registry_metadata_from_snapshot,
    resolve_sources,
    resolve_targets,
    resolver_for,
)
from skillforge.knowledge.staleness import (
    KnowledgeStalenessReport,
    KnowledgeStalenessStatus,
    check_knowledge_ref,
)
from skillforge.knowledge.store import (
    KnowledgeEntry,
    KnowledgeStore,
    StoreCorruptedError,
    StoreError,
    StoreLockedError,
    StoreValidation,
    knowledge_home,
    technology_slug,
)
from skillforge.knowledge.taxonomy import (
    DEFAULT_DOMAINS,
    detect_domains,
    domain_ids,
    split_identifier,
    tokenize_for_search,
)
from skillforge.knowledge.versions import detect_pin, normalize_version, versions_agree

__all__ = [
    "COMMUNITY_HOSTS",
    "DEFAULT_DOMAINS",
    "PACKAGE_ECOSYSTEMS",
    "ArtifactLocation",
    "AuthorityEntry",
    "AuthorityMap",
    "Budgets",
    "CandidateOrigin",
    "InstalledArtifact",
    "InstalledIndex",
    "IntrospectionResult",
    "KnowledgeEntry",
    "KnowledgeIndex",
    "KnowledgeStalenessReport",
    "KnowledgeStalenessStatus",
    "KnowledgeStore",
    "LockedEntry",
    "OfficialSourcePolicy",
    "ParsedLockfile",
    "RegistryMetadata",
    "ResolutionResult",
    "SourceCandidate",
    "SourceResolver",
    "StoreCorruptedError",
    "StoreError",
    "StoreLockedError",
    "StoreValidation",
    "build_index_payloads",
    "build_inventory",
    "capability_domain_counts",
    "capability_kind_counts",
    "check_knowledge_ref",
    "classify_role",
    "collect_installed",
    "default_authority_map",
    "detect_domains",
    "detect_pin",
    "domain_ids",
    "drafts_to_capabilities",
    "host_matches",
    "introspect_artifact",
    "introspector_for",
    "knowledge_home",
    "locate_artifact",
    "match_version_in_url",
    "normalize_capabilities",
    "normalize_capability",
    "normalize_version",
    "parse_lockfile",
    "parse_scan_lockfiles",
    "parser_for",
    "registry_metadata_from_snapshot",
    "resolve_inventory",
    "resolve_repository",
    "resolve_sources",
    "resolve_targets",
    "resolver_for",
    "split_identifier",
    "technology_slug",
    "tokenize_for_search",
    "versions_agree",
    "write_indexes",
]
