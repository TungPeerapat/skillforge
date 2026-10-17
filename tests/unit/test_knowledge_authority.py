"""Authority map and source-policy tests (K3)."""

from __future__ import annotations

import pytest

from skillforge.knowledge.authority import (
    AuthorityMap,
    CandidateOrigin,
    OfficialSourcePolicy,
    SourceCandidate,
    default_authority_map,
    host_matches,
    is_private_host,
    match_version_in_url,
    render_template,
)
from skillforge.models import (
    SourceAuthority,
    SourceVerificationKind,
    VersionMatchKind,
)


def test_default_map_loads_versioned_and_supports_aliases() -> None:
    authority_map = default_authority_map()
    assert authority_map.version == "2026-10-04"
    fastapi = authority_map.lookup("pypi", "FastAPI")
    assert fastapi is not None
    assert "fastapi.tiangolo.com" in fastapi.hosts
    assert fastapi.tag_template is not None
    prisma_client = authority_map.lookup("npm", "@prisma/client")
    assert prisma_client is not None
    assert prisma_client.package == "prisma"


def test_custom_map_parsing() -> None:
    authority_map = AuthorityMap.load_text(
        """
version = "test-1"
[[entry]]
ecosystem = "pypi"
package = "demo"
hosts = ["docs.demo.example"]
repository = "https://github.com/example/demo"
tag_template = "https://github.com/example/demo/releases/tag/{version}"
"""
    )
    assert authority_map.version == "test-1"
    entry = authority_map.lookup("pypi", "demo")
    assert entry is not None
    assert authority_map.hosts_for("pypi", "DEMO") == ("docs.demo.example",)
    assert authority_map.lookup("npm", "demo") is None


@pytest.mark.parametrize(
    ("url", "version", "expected"),
    [
        ("https://github.com/x/y/releases/tag/v1.2.3", "1.2.3", VersionMatchKind.EXACT),
        ("https://github.com/x/y/releases/tag/1.2.3", "1.2.3", VersionMatchKind.EXACT),
        ("https://guava.dev/releases/33.0.0/api/docs/", "33.0.0", VersionMatchKind.EXACT),
        ("https://docs.pydantic.dev/2.9/", "2.9.2", VersionMatchKind.COMPATIBLE),
        ("https://example.com/8.2.x/guide", "8.2.1", VersionMatchKind.COMPATIBLE),
        ("https://example.com/3/reference", "3.1.0", VersionMatchKind.MAJOR_MATCH),
        ("https://github.com/x/y/tree/v2.0.0", "1.0.0", VersionMatchKind.MISMATCH),
        ("https://docs.example.org/9.9/", "1.0.0", VersionMatchKind.MISMATCH),
        ("https://example.com/stable/", "1.0.0", VersionMatchKind.LATEST_ONLY),
        ("https://example.com/", "1.0.0", VersionMatchKind.LATEST_ONLY),
    ],
)
def test_version_matching(url: str, version: str, expected: VersionMatchKind) -> None:
    assert match_version_in_url(url, version) is expected


def test_render_template_placeholders() -> None:
    rendered = render_template(
        "https://docs.example/{major}.{minor}/api/{version}/v{version_dashed}",
        "2.9.2",
    )
    assert rendered == "https://docs.example/2.9/api/2.9.2/v2_9_2"


def test_prerelease_docs_are_not_exact_for_release() -> None:
    assert (
        match_version_in_url("https://docs.example/1.2.3-beta/", "1.2.3")
        is VersionMatchKind.MISMATCH
    )
    assert (
        match_version_in_url("https://docs.example/1.2.3/", "1.2.3-beta")
        is VersionMatchKind.MISMATCH
    )
    assert render_template("https://example.com/v{version}", "v1.2.3").endswith("/v1.2.3")


def test_host_matching_rejects_lookalikes() -> None:
    assert host_matches("fastapi.tiangolo.com", "fastapi.tiangolo.com")
    assert host_matches("docs.fastapi.tiangolo.com", "fastapi.tiangolo.com")
    assert not host_matches("fastapi.tiangolo.com.evil.com", "fastapi.tiangolo.com")
    assert not host_matches("evilfastapi.tiangolo.com", "fastapi.tiangolo.com")


@pytest.mark.parametrize(
    "host",
    ["localhost", "127.0.0.1", "10.0.0.5", "192.168.1.10", "172.16.9.1", "foo.local", "::1"],
)
def test_private_hosts_detected(host: str) -> None:
    assert is_private_host(host)


@pytest.mark.parametrize("host", ["fastapi.tiangolo.com", "8.8.8.8", "docs.example.org"])
def test_public_hosts_allowed(host: str) -> None:
    assert not is_private_host(host)


def _policy() -> OfficialSourcePolicy:
    return OfficialSourcePolicy()


def test_map_candidate_requires_curated_host() -> None:
    candidate = SourceCandidate(
        "https://evil.example.com/fastapi",
        SourceAuthority.EXACT_DOCS,
        CandidateOrigin.MAP,
        curated_hosts=("fastapi.tiangolo.com",),
    )
    decision = _policy().evaluate(
        candidate, technology="fastapi", version="0.115.0", curated_hosts=("fastapi.tiangolo.com",)
    )
    assert decision.accepted is False
    assert decision.verification[0].kind is SourceVerificationKind.REJECTED_UNKNOWN_HOST
    assert decision.verification[0].detail


def test_spoofed_lookalike_host_is_rejected() -> None:
    candidate = SourceCandidate(
        "https://fastapi.tiangolo.com.evil.com/docs",
        SourceAuthority.EXACT_DOCS,
        CandidateOrigin.MAP,
        curated_hosts=("fastapi.tiangolo.com",),
    )
    decision = _policy().evaluate(
        candidate, technology="fastapi", version="0.115.0", curated_hosts=("fastapi.tiangolo.com",)
    )
    assert decision.accepted is False
    assert decision.verification[0].kind is SourceVerificationKind.REJECTED_UNKNOWN_HOST


def test_curated_release_candidate_accepted_with_evidence() -> None:
    candidate = SourceCandidate(
        "https://github.com/fastapi/fastapi/releases/tag/0.115.0",
        SourceAuthority.OFFICIAL_REPOSITORY_TAG,
        CandidateOrigin.MAP,
        extra=(SourceVerificationKind.RELEASE_TAG_MATCH,),
        curated_hosts=("github.com",),
    )
    decision = _policy().evaluate(
        candidate, technology="fastapi", version="0.115.0", curated_hosts=("github.com",)
    )
    assert decision.accepted is True
    assert decision.version_match is VersionMatchKind.EXACT
    kinds = {item.kind for item in decision.verification}
    assert SourceVerificationKind.REGISTRY_DOMAIN_MAP in kinds
    assert SourceVerificationKind.RELEASE_TAG_MATCH in kinds
    assert all(item.detail for item in decision.verification)


def test_http_scheme_is_rejected() -> None:
    candidate = SourceCandidate(
        "http://fastapi.tiangolo.com/",
        SourceAuthority.EXACT_DOCS,
        CandidateOrigin.MAP,
        curated_hosts=("fastapi.tiangolo.com",),
    )
    decision = _policy().evaluate(
        candidate, technology="fastapi", version="0.115.0", curated_hosts=("fastapi.tiangolo.com",)
    )
    assert decision.accepted is False
    assert decision.verification[0].kind is SourceVerificationKind.REJECTED_INSECURE_SCHEME


def test_community_host_is_rejected() -> None:
    candidate = SourceCandidate(
        "https://stackoverflow.com/questions/123/what-is-fastapi",
        SourceAuthority.OFFICIAL_PACKAGE_METADATA,
        CandidateOrigin.REGISTRY_METADATA,
    )
    decision = _policy().evaluate(candidate, technology="fastapi", version="0.115.0")
    assert decision.accepted is False
    assert decision.authority is SourceAuthority.THIRD_PARTY
    assert decision.verification[0].kind is SourceVerificationKind.REJECTED_COMMUNITY
    assert decision.verification[0].detail


def test_private_host_is_rejected() -> None:
    candidate = SourceCandidate(
        "https://127.0.0.1/docs",
        SourceAuthority.EXACT_DOCS,
        CandidateOrigin.MAP,
        curated_hosts=("127.0.0.1",),
    )
    decision = _policy().evaluate(
        candidate, technology="fastapi", version="0.115.0", curated_hosts=("127.0.0.1",)
    )
    assert decision.accepted is False
    assert decision.verification[0].kind is SourceVerificationKind.REJECTED_UNKNOWN_HOST


def test_declared_candidate_accepted_and_map_verification_added() -> None:
    candidate = SourceCandidate(
        "https://docs.djangoproject.com/en/5.0/",
        SourceAuthority.OFFICIAL_GENERATED_REFERENCE,
        CandidateOrigin.DECLARED_DOCUMENTATION,
        curated_hosts=("docs.djangoproject.com",),
    )
    decision = _policy().evaluate(
        candidate,
        technology="django",
        version="5.0.4",
        curated_hosts=("docs.djangoproject.com",),
    )
    assert decision.accepted is True
    assert decision.version_match is VersionMatchKind.COMPATIBLE
    kinds = {item.kind for item in decision.verification}
    assert SourceVerificationKind.DECLARED_DOCUMENTATION in kinds
    assert SourceVerificationKind.REGISTRY_DOMAIN_MAP in kinds
