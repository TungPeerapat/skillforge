"""Curated official-source authority map and policy (K3).

Offline by design: this module generates *candidate* URLs from a versioned
curated TOML map and validates them — it never fetches anything. Fetching is
K7, behind explicit consent. Security rules from the plan (§25):

* HTTPS only; ``http`` is rejected with a recorded reason;
* community hosts (Q&A, blogs, mirrors) are never authoritative;
* loopback/private hosts are rejected outright (SSRF guard);
* map candidates must match the curated host set for their package (spoofed
  lookalike hosts such as ``fastapi.tiangolo.com.evil.com`` are rejected).
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from importlib import resources
from ipaddress import ip_address
from urllib.parse import urlsplit

from skillforge.models import (
    SourceAuthority,
    SourceVerification,
    SourceVerificationKind,
    VersionMatchKind,
)

_AUTHORITY_PACKAGE = "skillforge.knowledge.data"
_AUTHORITY_FILE = "authority.toml"

#: Hosts that are never authoritative (Q&A, blogs, mirrors, social).
COMMUNITY_HOSTS: tuple[str, ...] = (
    "stackoverflow.com",
    "stackexchange.com",
    "superuser.com",
    "serverfault.com",
    "medium.com",
    "dev.to",
    "hashnode.dev",
    "substack.com",
    "wordpress.com",
    "blogspot.com",
    "reddit.com",
    "quora.com",
    "geeksforgeeks.org",
    "tutorialspoint.com",
    "w3schools.com",
    "javatpoint.com",
    "youtube.com",
    "facebook.com",
    "x.com",
    "twitter.com",
    "linkedin.com",
    "pinterest.com",
)


@dataclass(frozen=True, slots=True)
class AuthorityEntry:
    """One curated package -> official hosts/repository/docs mapping."""

    ecosystem: str
    package: str
    hosts: tuple[str, ...] = ()
    repository: str | None = None
    tag_template: str | None = None
    docs: str | None = None
    docs_version_template: str | None = None
    api: str | None = None
    api_version_template: str | None = None
    changelog: str | None = None
    migration: str | None = None
    aliases: tuple[str, ...] = ()

    def names(self) -> tuple[str, ...]:
        return (self.package, *self.aliases)

    @property
    def allowed_hosts(self) -> tuple[str, ...]:
        """Curated docs hosts plus the official repository host, if any."""
        hosts = list(self.hosts)
        if self.repository:
            hostname = urlsplit(self.repository).hostname
            if hostname:
                hosts.append(hostname.lower())
        return tuple(hosts)


class AuthorityMap:
    """Versioned curated map; lookup is exact and case-insensitive."""

    def __init__(self, version: str, entries: tuple[AuthorityEntry, ...]) -> None:
        self.version = version
        self.entries = entries
        self._index: dict[tuple[str, str], AuthorityEntry] = {}
        for entry in entries:
            for name in entry.names():
                self._index[(entry.ecosystem, name.lower())] = entry

    def lookup(self, ecosystem: str, technology: str) -> AuthorityEntry | None:
        return self._index.get((ecosystem, technology.lower()))

    def hosts_for(self, ecosystem: str, technology: str) -> tuple[str, ...]:
        entry = self.lookup(ecosystem, technology)
        return entry.hosts if entry is not None else ()

    @classmethod
    def load_text(cls, text: str) -> AuthorityMap:
        data = tomllib.loads(text)
        version = str(data.get("version") or "unversioned")
        entries: list[AuthorityEntry] = []
        for raw in data.get("entry", []):
            entries.append(
                AuthorityEntry(
                    ecosystem=str(raw["ecosystem"]).lower(),
                    package=str(raw["package"]),
                    hosts=tuple(str(item).lower() for item in raw.get("hosts", [])),
                    repository=raw.get("repository"),
                    tag_template=raw.get("tag_template"),
                    docs=raw.get("docs"),
                    docs_version_template=raw.get("docs_version_template"),
                    api=raw.get("api"),
                    api_version_template=raw.get("api_version_template"),
                    changelog=raw.get("changelog"),
                    migration=raw.get("migration"),
                    aliases=tuple(str(item) for item in raw.get("aliases", [])),
                )
            )
        return cls(version, tuple(entries))

    @classmethod
    def load_default(cls) -> AuthorityMap:
        text = (
            resources.files(_AUTHORITY_PACKAGE)
            .joinpath(_AUTHORITY_FILE)
            .read_text(encoding="utf-8")
        )
        return cls.load_text(text)


@lru_cache(maxsize=1)
def default_authority_map() -> AuthorityMap:
    return AuthorityMap.load_default()


# --------------------------------------------------------------------- hosts


def host_matches(host: str, domain: str) -> bool:
    """Exact host or any subdomain of it; lookalikes never match."""
    cleaned_host = host.lower().rstrip(".")
    cleaned_domain = domain.lower().rstrip(".")
    return cleaned_host == cleaned_domain or cleaned_host.endswith("." + cleaned_domain)


def host_matches_any(host: str, domains: tuple[str, ...] | list[str]) -> bool:
    return any(host_matches(host, domain) for domain in domains)


def is_community_host(host: str, community: tuple[str, ...] = COMMUNITY_HOSTS) -> bool:
    return host_matches_any(host, community)


def is_private_host(host: str) -> bool:
    """SSRF guard: loopback, private, link-local, and .local hosts are refused."""
    cleaned = host.strip("[]").lower().rstrip(".")
    if cleaned == "localhost" or cleaned.endswith(
        (".localhost", ".local", ".internal", ".home.arpa")
    ):
        return True
    try:
        parsed = ip_address(cleaned)
    except ValueError:
        return False
    return (
        parsed.is_private
        or parsed.is_loopback
        or parsed.is_link_local
        or parsed.is_reserved
        or parsed.is_multicast
        or parsed.is_unspecified
    )


# ------------------------------------------------------------ version matching

_VERSION_TOKEN_RE = re.compile(
    r"^v?(?P<version>\d+(?:\.\d+){0,2})(?P<suffix>[.\-+][0-9A-Za-z][0-9A-Za-z.\-]*)?$"
)
_WILDCARD_TOKEN_RE = re.compile(r"^v?(?P<head>\d+(?:\.\d+)?)\.(?P<wild>[*xX])$")


def version_parts(version: str) -> tuple[int, ...]:
    """Leading numeric components of a version (``v1.2.3-beta`` -> ``(1, 2, 3)``)."""
    parts: list[int] = []
    for chunk in re.split(r"[.\-+]", version.strip().lstrip("vV")):
        if chunk.isdigit():
            parts.append(int(chunk))
        else:
            break
    return tuple(parts)


def render_template(template: str, version: str) -> str:
    version = version.removeprefix("v")
    parts = version_parts(version)
    major = parts[0] if parts else 0
    minor = parts[1] if len(parts) > 1 else 0
    return template.format(
        version=version,
        major=major,
        minor=minor,
        version_dashed=version.replace(".", "_"),
    )


def _prefix_matches(head: tuple[int, ...], target: tuple[int, ...]) -> bool:
    return len(head) <= len(target) and target[: len(head)] == head


def _token_match(token: str, target: tuple[int, ...], version: str) -> VersionMatchKind | None:
    wildcard = _WILDCARD_TOKEN_RE.match(token)
    if wildcard:
        head = tuple(int(part) for part in wildcard.group("head").split("."))
        return (
            VersionMatchKind.COMPATIBLE
            if _prefix_matches(head, target)
            else VersionMatchKind.MISMATCH
        )
    match = _VERSION_TOKEN_RE.match(token)
    if match is None:
        return None
    numbers = tuple(int(part) for part in match.group("version").split("."))
    if len(numbers) >= 3:
        return (
            VersionMatchKind.EXACT
            if token.removeprefix("v") == version.removeprefix("v")
            else VersionMatchKind.MISMATCH
        )
    if len(numbers) == 2:
        if len(target) >= 2 and numbers == target[:2]:
            return VersionMatchKind.COMPATIBLE
        return VersionMatchKind.MISMATCH
    if target and numbers[0] == target[0]:
        return VersionMatchKind.MAJOR_MATCH
    return VersionMatchKind.MISMATCH


def match_version_in_url(url: str, version: str) -> VersionMatchKind:
    """Classify how a URL relates to a resolved version.

    Heuristic and deterministic: path segments that look like versions are
    compared component-wise. No version signal at all means ``LATEST_ONLY``
    (the page documents the current release, not this exact version).
    """
    target = version_parts(version)
    if not target:
        return VersionMatchKind.LATEST_ONLY
    best: VersionMatchKind | None = None
    for token in urlsplit(url).path.split("/"):
        if not token:
            continue
        kind = _token_match(token, target, version)
        if kind is None:
            continue
        if best is None or kind.rank < best.rank:
            best = kind
    return best or VersionMatchKind.LATEST_ONLY


# -------------------------------------------------------------------- policy


class CandidateOrigin(StrEnum):
    """Where a candidate URL came from (determines what counts as evidence)."""

    MAP = "map"
    REGISTRY_METADATA = "registry_metadata"
    DECLARED_HOMEPAGE = "declared_homepage"
    DECLARED_REPOSITORY = "declared_repository"
    DECLARED_DOCUMENTATION = "declared_documentation"


_ORIGIN_VERIFICATION = {
    CandidateOrigin.MAP.value: SourceVerificationKind.REGISTRY_DOMAIN_MAP,
    CandidateOrigin.REGISTRY_METADATA.value: SourceVerificationKind.REGISTRY_METADATA_MATCH,
    CandidateOrigin.DECLARED_HOMEPAGE.value: SourceVerificationKind.DECLARED_HOMEPAGE,
    CandidateOrigin.DECLARED_REPOSITORY.value: SourceVerificationKind.DECLARED_REPOSITORY,
    CandidateOrigin.DECLARED_DOCUMENTATION.value: SourceVerificationKind.DECLARED_DOCUMENTATION,
}


def _origin_kind(origin: CandidateOrigin) -> SourceVerificationKind:
    return _ORIGIN_VERIFICATION[origin.value]


@dataclass(frozen=True, slots=True)
class SourceCandidate:
    """A proposed source URL plus where it came from."""

    url: str
    authority: SourceAuthority
    origin: CandidateOrigin
    title: str = ""
    extra: tuple[SourceVerificationKind, ...] = ()
    curated_hosts: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    accepted: bool
    authority: SourceAuthority
    version_match: VersionMatchKind
    verification: tuple[SourceVerification, ...]

    @property
    def rejection_reason(self) -> str:
        for item in self.verification:
            if item.kind.is_rejection:
                return item.detail
        return ""


class OfficialSourcePolicy:
    """Deterministic accept/reject rules for candidate sources."""

    def __init__(
        self,
        authority_map: AuthorityMap | None = None,
        community_hosts: tuple[str, ...] = COMMUNITY_HOSTS,
    ) -> None:
        self.authority_map = authority_map or default_authority_map()
        self.community_hosts = community_hosts

    def evaluate(
        self,
        candidate: SourceCandidate,
        *,
        technology: str,
        version: str,
        curated_hosts: tuple[str, ...] = (),
    ) -> PolicyDecision:
        match = match_version_in_url(candidate.url, version)
        parts = urlsplit(candidate.url)
        scheme = parts.scheme.lower()
        host = (parts.hostname or "").lower()
        if scheme != "https":
            return self._reject(
                candidate,
                match,
                SourceVerificationKind.REJECTED_INSECURE_SCHEME,
                f"source URLs must use https (got '{scheme or 'none'}')",
            )
        if not host:
            return self._reject(
                candidate,
                match,
                SourceVerificationKind.REJECTED_UNKNOWN_HOST,
                "URL has no host",
            )
        if is_private_host(host):
            return self._reject(
                candidate,
                match,
                SourceVerificationKind.REJECTED_UNKNOWN_HOST,
                f"private or loopback host '{host}' is never an official source",
            )
        if is_community_host(host, self.community_hosts):
            return self._reject(
                candidate,
                match,
                SourceVerificationKind.REJECTED_COMMUNITY,
                f"community host '{host}' is not an authoritative source",
                authority=SourceAuthority.THIRD_PARTY,
            )
        allowed = candidate.curated_hosts or curated_hosts
        if candidate.origin is CandidateOrigin.MAP:
            if not host_matches_any(host, allowed):
                return self._reject(
                    candidate,
                    match,
                    SourceVerificationKind.REJECTED_UNKNOWN_HOST,
                    f"host '{host}' is not in the curated authority map for {technology}",
                )
        elif candidate.origin not in _ORIGIN_VERIFICATION:
            return self._reject(
                candidate,
                match,
                SourceVerificationKind.REJECTED_UNKNOWN_HOST,
                f"unverified candidate origin '{candidate.origin.value}'",
            )

        verifications = [
            SourceVerification(
                kind=_origin_kind(candidate.origin),
                detail=f"candidate comes from {candidate.origin.value}",
            )
        ]
        if candidate.origin is not CandidateOrigin.MAP and host_matches_any(host, allowed):
            verifications.append(
                SourceVerification(
                    kind=SourceVerificationKind.REGISTRY_DOMAIN_MAP,
                    detail=f"host '{host}' is in the curated authority map for {technology}",
                )
            )
        for extra in candidate.extra:
            verifications.append(
                SourceVerification(
                    kind=extra,
                    detail=_extra_detail(extra, technology, version),
                )
            )
        return PolicyDecision(True, candidate.authority, match, tuple(verifications))

    def rejection(
        self,
        *,
        kind: SourceVerificationKind,
        detail: str,
        authority: SourceAuthority = SourceAuthority.UNKNOWN,
        version_match: VersionMatchKind = VersionMatchKind.UNKNOWN,
    ) -> PolicyDecision:
        """Build a rejection decision that is not derived from a URL check."""
        return PolicyDecision(
            False,
            authority,
            version_match,
            (SourceVerification(kind=kind, detail=detail),),
        )

    def _reject(
        self,
        candidate: SourceCandidate,
        match: VersionMatchKind,
        kind: SourceVerificationKind,
        detail: str,
        authority: SourceAuthority = SourceAuthority.UNKNOWN,
    ) -> PolicyDecision:
        return PolicyDecision(
            False,
            authority,
            match,
            (SourceVerification(kind=kind, detail=detail),),
        )


def _extra_detail(kind: SourceVerificationKind, technology: str, version: str) -> str:
    if kind is SourceVerificationKind.RELEASE_TAG_MATCH:
        return f"curated release/tag URL for {technology} {version}"
    if kind is SourceVerificationKind.REGISTRY_DOMAIN_MAP:
        return f"host is in the curated authority map for {technology}"
    return ""


__all__ = [
    "COMMUNITY_HOSTS",
    "AuthorityEntry",
    "AuthorityMap",
    "CandidateOrigin",
    "OfficialSourcePolicy",
    "PolicyDecision",
    "SourceCandidate",
    "default_authority_map",
    "host_matches",
    "host_matches_any",
    "is_community_host",
    "is_private_host",
    "match_version_in_url",
    "render_template",
    "version_parts",
]
