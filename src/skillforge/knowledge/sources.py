"""Official-source candidate resolution (K3).

Turns a :class:`~skillforge.models.knowledge.KnowledgeTarget` into
:class:`~skillforge.models.knowledge.KnowledgeSource` records using only local
evidence:

* the curated authority map (official hosts, repository/tag templates, docs);
* URLs declared inside installed artifacts (``dist-info/METADATA``,
  ``package.json``);
* registry metadata **snapshots** supplied by the caller (fetching is K7 and
  requires explicit consent).

Every candidate passes :class:`OfficialSourcePolicy`; rejected candidates are
still returned, each with a recorded reason. Nothing here performs I/O beyond
the caller-provided data.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from skillforge.knowledge.artifacts import (
    InstalledArtifact,
    InstalledIndex,
    clean_repository_url,
    role_for_url_label,
)
from skillforge.knowledge.authority import (
    AuthorityMap,
    CandidateOrigin,
    OfficialSourcePolicy,
    SourceCandidate,
    default_authority_map,
    match_version_in_url,
    render_template,
)
from skillforge.knowledge.versions import versions_agree
from skillforge.models import (
    KnowledgeSource,
    KnowledgeTarget,
    SourceAuthority,
    SourceVerification,
    SourceVerificationKind,
    VersionMatchKind,
)

#: Declared/registry URL role -> (origin, authority) mapping.
_ROLE_SOURCES: dict[str, tuple[CandidateOrigin, SourceAuthority]] = {
    "homepage": (CandidateOrigin.DECLARED_HOMEPAGE, SourceAuthority.OFFICIAL_PACKAGE_METADATA),
    "repository": (CandidateOrigin.DECLARED_REPOSITORY, SourceAuthority.OFFICIAL_REPOSITORY_TAG),
    "documentation": (
        CandidateOrigin.DECLARED_DOCUMENTATION,
        SourceAuthority.OFFICIAL_GENERATED_REFERENCE,
    ),
    "changelog": (
        CandidateOrigin.DECLARED_DOCUMENTATION,
        SourceAuthority.OFFICIAL_CHANGELOG,
    ),
    "package": (
        CandidateOrigin.REGISTRY_METADATA,
        SourceAuthority.OFFICIAL_PACKAGE_METADATA,
    ),
}


@dataclass(frozen=True, slots=True)
class RegistryMetadata:
    """Normalized registry snapshot for one exact package version."""

    ecosystem: str
    name: str
    version: str
    urls: dict[str, list[str]] = field(default_factory=dict)
    source: str = "registry_snapshot"


def _add_url(mapping: dict[str, list[str]], role: str, url: object) -> None:
    if not isinstance(url, str) or not url.strip():
        return
    cleaned = url.strip()
    if role == "repository":
        cleaned = clean_repository_url(cleaned)
    mapping.setdefault(role, []).append(cleaned)


def _repository_url(value: Any) -> object:
    if isinstance(value, Mapping):
        return value.get("url")
    return value


def registry_metadata_from_snapshot(
    ecosystem: str, snapshot: Mapping[str, Any] | None
) -> RegistryMetadata | None:
    """Normalize an ecosystem-specific registry snapshot; never fetches."""
    if not isinstance(snapshot, Mapping):
        return None
    urls: dict[str, list[str]] = {}
    name: object = None
    version: object = None
    if ecosystem == "pypi":
        info = snapshot.get("info")
        if not isinstance(info, Mapping):
            return None
        name = info.get("name")
        version = info.get("version")
        _add_url(urls, "homepage", info.get("home_page"))
        project_urls = info.get("project_urls")
        if isinstance(project_urls, Mapping):
            for label, url in project_urls.items():
                _add_url(urls, role_for_url_label(str(label)), url)
        _add_url(urls, "package", info.get("package_url"))
    elif ecosystem == "npm":
        name = snapshot.get("name")
        version = snapshot.get("version")
        _add_url(urls, "homepage", snapshot.get("homepage"))
        _add_url(urls, "repository", _repository_url(snapshot.get("repository")))
        _add_url(urls, "issues", _repository_url(snapshot.get("bugs")))
        _add_url(urls, "documentation", snapshot.get("documentation"))
    else:
        name = snapshot.get("name") or snapshot.get("title") or snapshot.get("id")
        version = snapshot.get("version")
        for key in ("homepage", "projectUrl", "repositoryUrl", "repository_url"):
            _add_url(urls, "homepage", snapshot.get(key))
        _add_url(urls, "repository", _repository_url(snapshot.get("repository")))
        _add_url(urls, "documentation", snapshot.get("documentation"))
    if not name or not version:
        return None
    return RegistryMetadata(
        ecosystem=ecosystem,
        name=str(name),
        version=str(version),
        urls=urls,
        source=str(snapshot.get("source") or "registry_snapshot"),
    )


def _documentation_authority(url: str, version: str) -> SourceAuthority:
    return (
        SourceAuthority.EXACT_DOCS
        if match_version_in_url(url, version).is_exact
        else SourceAuthority.OFFICIAL_GENERATED_REFERENCE
    )


def _api_authority(url: str, version: str) -> SourceAuthority:
    return (
        SourceAuthority.EXACT_API_REFERENCE
        if match_version_in_url(url, version).is_exact
        else SourceAuthority.OFFICIAL_GENERATED_REFERENCE
    )


class SourceResolver:
    """Offline source resolution for one ecosystem (the pipeline is shared)."""

    ecosystem = "unknown"

    def registry_metadata(self, snapshot: Mapping[str, Any] | None) -> RegistryMetadata | None:
        return registry_metadata_from_snapshot(self.ecosystem, snapshot)

    def resolve(
        self,
        target: KnowledgeTarget,
        *,
        declarations: InstalledArtifact | None = None,
        registry: RegistryMetadata | None = None,
        authority_map: AuthorityMap | None = None,
        policy: OfficialSourcePolicy | None = None,
    ) -> list[KnowledgeSource]:
        if target.ecosystem != self.ecosystem and self.ecosystem != "unknown":
            raise ValueError(f"{type(self).__name__} cannot resolve {target.ecosystem} targets")
        authority_map = authority_map or default_authority_map()
        policy = policy or OfficialSourcePolicy(authority_map)
        entry = authority_map.lookup(target.ecosystem, target.technology)
        curated_hosts = entry.allowed_hosts if entry is not None else ()
        version = target.version
        candidates: list[SourceCandidate] = []

        def add(
            url: str | None,
            authority: SourceAuthority,
            origin: CandidateOrigin,
            *,
            title: str = "",
            extra: tuple[SourceVerificationKind, ...] = (),
        ) -> None:
            if not url:
                return
            if any(existing.url == url for existing in candidates):
                return
            candidates.append(
                SourceCandidate(url, authority, origin, title, tuple(extra), curated_hosts)
            )

        # 1) curated authority map -------------------------------------------
        if entry is not None:
            if entry.repository and entry.tag_template:
                add(
                    render_template(entry.tag_template, version),
                    SourceAuthority.OFFICIAL_REPOSITORY_TAG,
                    CandidateOrigin.MAP,
                    title=f"{target.technology} {version} release",
                    extra=(SourceVerificationKind.RELEASE_TAG_MATCH,),
                )
            if entry.repository:
                add(
                    entry.repository,
                    SourceAuthority.OFFICIAL_REPOSITORY_TAG,
                    CandidateOrigin.MAP,
                    title=f"{target.technology} repository",
                )
            if entry.docs:
                add(
                    entry.docs,
                    _documentation_authority(entry.docs, version),
                    CandidateOrigin.MAP,
                    title=f"{target.technology} documentation",
                )
            if entry.docs_version_template:
                add(
                    render_template(entry.docs_version_template, version),
                    SourceAuthority.EXACT_DOCS,
                    CandidateOrigin.MAP,
                    title=f"{target.technology} {version} documentation",
                )
            if entry.api:
                add(
                    entry.api,
                    _api_authority(entry.api, version),
                    CandidateOrigin.MAP,
                    title=f"{target.technology} API reference",
                )
            if entry.api_version_template:
                add(
                    render_template(entry.api_version_template, version),
                    SourceAuthority.EXACT_API_REFERENCE,
                    CandidateOrigin.MAP,
                    title=f"{target.technology} {version} API reference",
                )
            if entry.changelog:
                add(
                    entry.changelog,
                    SourceAuthority.OFFICIAL_CHANGELOG,
                    CandidateOrigin.MAP,
                    title=f"{target.technology} changelog",
                )
            if entry.migration:
                add(
                    entry.migration,
                    SourceAuthority.OFFICIAL_MIGRATION_GUIDE,
                    CandidateOrigin.MAP,
                    title=f"{target.technology} migration guide",
                )

        # 2) installed-artifact declarations ----------------------------------
        if declarations is not None:
            for role, urls in declarations.urls.items():
                mapping = _ROLE_SOURCES.get(role)
                if mapping is None:
                    continue
                origin, authority = mapping
                if origin is CandidateOrigin.REGISTRY_METADATA:
                    continue
                for url in urls:
                    add(
                        url,
                        authority,
                        origin,
                        title=f"declared {role} of {declarations.name}",
                    )

        # 3) registry metadata snapshot ---------------------------------------
        if registry is not None and versions_agree(target.ecosystem, registry.version, version):
            for role, urls in registry.urls.items():
                mapping = _ROLE_SOURCES.get(role)
                if mapping is None:
                    continue
                _origin, authority = mapping
                for url in urls:
                    add(
                        url,
                        authority,
                        CandidateOrigin.REGISTRY_METADATA,
                        title=f"registry {role} of {registry.name} {registry.version}",
                    )

        # 4) policy evaluation -------------------------------------------------
        sources: list[KnowledgeSource] = []
        for candidate in candidates:
            decision = policy.evaluate(
                candidate,
                technology=target.technology,
                version=version,
                curated_hosts=curated_hosts,
            )
            source = KnowledgeSource(
                id=KnowledgeSource.make_id(target.technology, version, candidate.url),
                technology=target.technology,
                version=version,
                url=candidate.url,
                title=candidate.title,
                authority=decision.authority,
                verification=list(decision.verification),
                version_match=decision.version_match,
                accepted=decision.accepted,
            )
            if decision.accepted and decision.version_match is VersionMatchKind.MISMATCH:
                source.notes.append(
                    "URL version segment does not match the resolved version; "
                    "excluded from capability claims (narrative context only)"
                )
            sources.append(source)

        if registry is not None and not versions_agree(target.ecosystem, registry.version, version):
            first_url = next((url for urls in registry.urls.values() for url in urls), None)
            if first_url:
                sources.append(
                    KnowledgeSource(
                        id=KnowledgeSource.make_id(target.technology, version, first_url),
                        technology=target.technology,
                        version=version,
                        url=first_url,
                        title=f"registry snapshot for {registry.name} {registry.version}",
                        authority=SourceAuthority.UNKNOWN,
                        verification=[
                            SourceVerification(
                                kind=SourceVerificationKind.REJECTED_VERSION_MISMATCH,
                                detail=(
                                    f"registry snapshot is for {registry.name} "
                                    f"{registry.version}, target is {version}"
                                ),
                            )
                        ],
                        version_match=VersionMatchKind.MISMATCH,
                        accepted=False,
                    )
                )

        sources.sort(
            key=lambda item: (
                0 if item.accepted else 1,
                item.authority.rank,
                item.version_match.rank,
                item.url,
            )
        )
        return sources


class PythonSourceResolver(SourceResolver):
    ecosystem = "pypi"


class NodeSourceResolver(SourceResolver):
    ecosystem = "npm"


class NuGetSourceResolver(SourceResolver):
    ecosystem = "nuget"


class GoSourceResolver(SourceResolver):
    ecosystem = "go"


class PubSourceResolver(SourceResolver):
    ecosystem = "pub"


class MavenSourceResolver(SourceResolver):
    ecosystem = "maven"


_SOURCE_RESOLVERS: dict[str, SourceResolver] = {
    resolver.ecosystem: resolver
    for resolver in (
        PythonSourceResolver(),
        NodeSourceResolver(),
        NuGetSourceResolver(),
        GoSourceResolver(),
        PubSourceResolver(),
        MavenSourceResolver(),
    )
}


def resolver_for(ecosystem: str) -> SourceResolver:
    return _SOURCE_RESOLVERS.get(ecosystem, SourceResolver())


def resolve_sources(
    target: KnowledgeTarget,
    *,
    declarations: InstalledArtifact | None = None,
    registry: RegistryMetadata | None = None,
    authority_map: AuthorityMap | None = None,
    policy: OfficialSourcePolicy | None = None,
) -> list[KnowledgeSource]:
    """Resolve candidate sources for one target (offline)."""
    return resolver_for(target.ecosystem).resolve(
        target,
        declarations=declarations,
        registry=registry,
        authority_map=authority_map,
        policy=policy,
    )


def resolve_targets(
    targets: list[KnowledgeTarget],
    *,
    installed: InstalledIndex | None = None,
    registry: Mapping[tuple[str, str], RegistryMetadata] | None = None,
    authority_map: AuthorityMap | None = None,
    policy: OfficialSourcePolicy | None = None,
) -> list[KnowledgeSource]:
    """Resolve every target, deduplicating identical source URLs."""
    authority_map = authority_map or default_authority_map()
    policy = policy or OfficialSourcePolicy(authority_map)
    sources: list[KnowledgeSource] = []
    seen: set[str] = set()
    for target in targets:
        declarations = None
        if installed is not None:
            declarations = installed.artifacts.get((target.ecosystem, target.technology.lower()))
        snapshot = None
        if registry is not None:
            snapshot = registry.get((target.ecosystem, target.technology.lower()))
        for source in resolve_sources(
            target,
            declarations=declarations,
            registry=snapshot,
            authority_map=authority_map,
            policy=policy,
        ):
            if source.id in seen:
                continue
            seen.add(source.id)
            sources.append(source)
    sources.sort(
        key=lambda item: (
            item.technology.lower(),
            item.version,
            0 if item.accepted else 1,
            item.authority.rank,
            item.version_match.rank,
            item.url,
        )
    )
    return sources


__all__ = [
    "GoSourceResolver",
    "MavenSourceResolver",
    "NodeSourceResolver",
    "NuGetSourceResolver",
    "PubSourceResolver",
    "PythonSourceResolver",
    "RegistryMetadata",
    "SourceResolver",
    "registry_metadata_from_snapshot",
    "resolve_sources",
    "resolve_targets",
    "resolver_for",
]
