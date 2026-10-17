"""Official-source resolution tests (K3): map, declarations, snapshots, rejections."""

from __future__ import annotations

from pathlib import Path

from skillforge.knowledge.artifacts import InstalledIndex, collect_installed
from skillforge.knowledge.sources import (
    registry_metadata_from_snapshot,
    resolve_sources,
    resolve_targets,
)
from skillforge.models import (
    KnowledgeTarget,
    SourceAuthority,
    SourceVerificationKind,
    VersionMatchKind,
)


def _pypi_snapshot(
    name: str, version: str, project_urls: dict[str, str] | None = None
) -> dict[str, object]:
    return {
        "info": {
            "name": name,
            "version": version,
            "home_page": "https://demo.example/",
            "project_urls": project_urls or {},
            "package_url": f"https://pypi.org/project/{name}/",
        }
    }


# ------------------------------------------------------------------ map path


def test_map_generates_exact_release_source_for_framework() -> None:
    sources = resolve_sources(
        KnowledgeTarget(ecosystem="pypi", technology="fastapi", version="0.115.0")
    )
    exact = [
        source
        for source in sources
        if source.accepted and source.version_match is VersionMatchKind.EXACT
    ]
    assert exact, "expected at least one exact source for fastapi"
    assert any(source.authority is SourceAuthority.OFFICIAL_REPOSITORY_TAG for source in exact)
    assert all(source.capability_eligible for source in exact)
    kinds = {item.kind for source in exact for item in source.verification}
    assert SourceVerificationKind.RELEASE_TAG_MATCH in kinds


def test_map_generates_exact_release_source_for_next() -> None:
    sources = resolve_sources(KnowledgeTarget(ecosystem="npm", technology="next", version="16.0.1"))
    assert any(
        source.accepted
        and source.version_match is VersionMatchKind.EXACT
        and "releases/tag/v16.0.1" in source.url
        for source in sources
    )


def test_uncovered_package_yields_no_sources() -> None:
    assert (
        resolve_sources(
            KnowledgeTarget(ecosystem="pypi", technology="totally-unknown-package", version="1.0.0")
        )
        == []
    )


def test_map_docs_latest_is_not_capability_eligible() -> None:
    sources = resolve_sources(
        KnowledgeTarget(ecosystem="pypi", technology="fastapi", version="0.115.0")
    )
    docs = [source for source in sources if source.url == "https://fastapi.tiangolo.com/"]
    assert docs
    assert docs[0].accepted is True
    assert docs[0].version_match is VersionMatchKind.LATEST_ONLY
    assert docs[0].capability_eligible is False


# ------------------------------------------------------- registry snapshots


def test_registry_snapshot_urls_become_sources() -> None:
    registry = registry_metadata_from_snapshot(
        "pypi",
        _pypi_snapshot(
            "demo-pkg",
            "1.2.3",
            {
                "Source": "https://github.com/example/demo-pkg",
                "Documentation": "https://docs.example.com/1.2.3/",
            },
        ),
    )
    assert registry is not None
    sources = resolve_sources(
        KnowledgeTarget(ecosystem="pypi", technology="demo-pkg", version="1.2.3"), registry=registry
    )
    by_url = {source.url: source for source in sources}
    doc = by_url["https://docs.example.com/1.2.3/"]
    assert doc.accepted is True
    assert doc.version_match is VersionMatchKind.EXACT
    assert any(
        item.kind is SourceVerificationKind.REGISTRY_METADATA_MATCH for item in doc.verification
    )
    repository = by_url["https://github.com/example/demo-pkg"]
    assert repository.version_match is VersionMatchKind.LATEST_ONLY
    package = by_url["https://pypi.org/project/demo-pkg/"]
    assert package.authority is SourceAuthority.OFFICIAL_PACKAGE_METADATA


def test_registry_snapshot_version_mismatch_is_rejected_with_reason() -> None:
    registry = registry_metadata_from_snapshot("pypi", _pypi_snapshot("demo-pkg", "9.9.9"))
    assert registry is not None
    sources = resolve_sources(
        KnowledgeTarget(ecosystem="pypi", technology="demo-pkg", version="1.2.3"), registry=registry
    )
    rejected = [source for source in sources if not source.accepted]
    assert rejected
    assert rejected[0].verification[0].kind is SourceVerificationKind.REJECTED_VERSION_MISMATCH
    assert "9.9.9" in rejected[0].rejection_reason
    assert all(not source.capability_eligible for source in rejected)


def test_community_registry_url_is_rejected() -> None:
    registry = registry_metadata_from_snapshot(
        "pypi",
        _pypi_snapshot(
            "demo-pkg",
            "1.2.3",
            {"Homepage": "https://stackoverflow.com/questions/1/demo"},
        ),
    )
    assert registry is not None
    sources = resolve_sources(
        KnowledgeTarget(ecosystem="pypi", technology="demo-pkg", version="1.2.3"), registry=registry
    )
    rejected = [
        source
        for source in sources
        if source.verification
        and source.verification[0].kind is SourceVerificationKind.REJECTED_COMMUNITY
    ]
    assert rejected
    assert rejected[0].authority is SourceAuthority.THIRD_PARTY
    assert rejected[0].rejection_reason


def test_insecure_registry_url_is_rejected() -> None:
    snapshot = {
        "info": {
            "name": "demo-pkg",
            "version": "1.2.3",
            "project_urls": {"Homepage": "http://demo.example/"},
        }
    }
    registry = registry_metadata_from_snapshot("pypi", snapshot)
    assert registry is not None
    sources = resolve_sources(
        KnowledgeTarget(ecosystem="pypi", technology="demo-pkg", version="1.2.3"), registry=registry
    )
    rejected = [
        source
        for source in sources
        if source.verification
        and source.verification[0].kind is SourceVerificationKind.REJECTED_INSECURE_SCHEME
    ]
    assert rejected
    assert rejected[0].rejection_reason


def test_npm_registry_snapshot_shape() -> None:
    registry = registry_metadata_from_snapshot(
        "npm",
        {
            "name": "demo-npm",
            "version": "3.0.0",
            "homepage": "https://demo-npm.dev/",
            "repository": {"type": "git", "url": "git+https://github.com/example/demo-npm.git"},
        },
    )
    assert registry is not None
    assert registry.urls["repository"] == ["https://github.com/example/demo-npm"]
    sources = resolve_sources(
        KnowledgeTarget(ecosystem="npm", technology="demo-npm", version="3.0.0"), registry=registry
    )
    repository = next(
        source for source in sources if source.url == "https://github.com/example/demo-npm"
    )
    assert repository.accepted is True
    assert any(
        item.kind is SourceVerificationKind.REGISTRY_METADATA_MATCH
        for item in repository.verification
    )


# ------------------------------------------------- installed artifact inputs


def _write_metadata(root: Path, name: str, version: str, lines: list[str]) -> None:
    path = (
        root
        / ".venv"
        / "lib"
        / "python3.12"
        / "site-packages"
        / f"{name}-{version}.dist-info"
        / "METADATA"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    body = [
        "Metadata-Version: 2.1",
        f"Name: {name}",
        f"Version: {version}",
        *lines,
        "",
        "Body",
    ]
    path.write_text("\n".join(body) + "\n", encoding="utf-8")


def test_installed_artifact_declarations_are_used(tmp_path: Path) -> None:
    _write_metadata(
        tmp_path,
        "demo-pkg",
        "1.2.3",
        [
            "Home-page: https://demo.example/",
            "Project-URL: Documentation, https://docs.demo.example/1.2.3/",
            "Project-URL: Source, git+https://github.com/example/demo-pkg.git",
        ],
    )
    index = collect_installed(tmp_path)
    artifact = index.artifacts[("pypi", "demo-pkg")]
    assert artifact.urls["homepage"] == ["https://demo.example/"]
    assert artifact.urls["repository"] == ["https://github.com/example/demo-pkg"]

    sources = resolve_sources(
        KnowledgeTarget(ecosystem="pypi", technology="demo-pkg", version="1.2.3"),
        declarations=artifact,
    )
    by_url = {source.url: source for source in sources}
    doc = by_url["https://docs.demo.example/1.2.3/"]
    assert doc.accepted is True
    assert doc.version_match is VersionMatchKind.EXACT
    assert any(
        item.kind is SourceVerificationKind.DECLARED_DOCUMENTATION for item in doc.verification
    )
    homepage = by_url["https://demo.example/"]
    assert homepage.version_match is VersionMatchKind.LATEST_ONLY
    assert any(
        item.kind is SourceVerificationKind.DECLARED_HOMEPAGE for item in homepage.verification
    )


def test_resolve_targets_uses_installed_index(tmp_path: Path) -> None:
    _write_metadata(
        tmp_path,
        "demo-pkg",
        "1.2.3",
        ["Project-URL: Documentation, https://docs.demo.example/1.2.3/"],
    )
    index = collect_installed(tmp_path)
    sources = resolve_targets(
        [KnowledgeTarget(ecosystem="pypi", technology="demo-pkg", version="1.2.3")], installed=index
    )
    assert any(
        source.url == "https://docs.demo.example/1.2.3/" and source.capability_eligible
        for source in sources
    )


def test_resolve_targets_deduplicates_same_target() -> None:
    target = KnowledgeTarget(ecosystem="pypi", technology="fastapi", version="0.115.0")
    single = resolve_sources(target)
    doubled = resolve_targets([target, target])
    assert len(doubled) == len(single)


def test_resolve_targets_empty_inputs() -> None:
    assert resolve_targets([]) == []
    assert (
        resolve_targets(
            [KnowledgeTarget(ecosystem="pypi", technology="unknown-pkg", version="1.0.0")],
            installed=InstalledIndex(),
        )
        == []
    )
