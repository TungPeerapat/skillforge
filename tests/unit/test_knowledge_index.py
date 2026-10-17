"""Structured index tests (K5): payloads, lookups, round trip, performance."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path

from skillforge.knowledge.capabilities import normalize_capability
from skillforge.knowledge.index import (
    BY_DOMAIN_FILENAME,
    BY_KIND_FILENAME,
    BY_NAME_FILENAME,
    BY_PARENT_FILENAME,
    POSTINGS_FILENAME,
    KnowledgeIndex,
    build_index_payloads,
    write_indexes,
)
from skillforge.knowledge.store import KnowledgeEntry, KnowledgeStore
from skillforge.models import (
    Capability,
    CapabilityKind,
    KnowledgeManifest,
    KnowledgeTarget,
)

_TARGET = KnowledgeTarget(ecosystem="pypi", technology="fastapi", version="0.115.0")


def _cap(
    name: str,
    qualified: str,
    *,
    kind: CapabilityKind = CapabilityKind.FUNCTION,
    description: str = "",
    parent_id: str | None = None,
    deprecated: bool | None = None,
) -> Capability:
    return normalize_capability(
        Capability(
            id="",
            technology="fastapi",
            ecosystem="pypi",
            version="0.115.0",
            kind=kind,
            name=name,
            qualified_name=qualified,
            description=description,
            parent_id=parent_id,
            deprecated=deprecated,
        )
    )


def _sample_caps() -> list[Capability]:
    return [
        _cap("JSONResponse", "fastapi.responses.JSONResponse", kind=CapabilityKind.CLASS),
        _cap(
            "HTTPBearer",
            "fastapi.security.HTTPBearer",
            kind=CapabilityKind.CLASS,
            description="HTTP bearer token authentication",
        ),
        _cap("Request", "fastapi.Request", kind=CapabilityKind.CLASS),
        _cap("RequestBuilder", "fastapi.RequestBuilder", kind=CapabilityKind.CLASS),
        _cap(
            "get",
            "fastapi.Request.get",
            kind=CapabilityKind.METHOD,
            parent_id=normalize_capability(
                Capability(
                    id="",
                    technology="fastapi",
                    ecosystem="pypi",
                    version="0.115.0",
                    kind=CapabilityKind.CLASS,
                    name="Request",
                    qualified_name="fastapi.Request",
                )
            ).id,
        ),
        _cap("old_route", "fastapi.old_route", deprecated=True),
    ]


def test_build_index_payloads_formats() -> None:
    caps = _sample_caps()
    payloads = build_index_payloads(caps)

    assert payloads["by_name"]["request"] == sorted(
        [cap.id for cap in caps if cap.name.lower() == "request"]
    )
    assert payloads["by_name"]["fastapi.requestbuilder"]
    assert payloads["by_kind"]["class"]
    assert payloads["by_domain"]["security"]  # HTTPBearer
    assert payloads["by_parent"]
    assert "bearer" in payloads["postings"]
    # Every bucket is sorted and deterministic.
    for mapping in payloads.values():
        for bucket in mapping.values():
            assert bucket == sorted(bucket)
    assert payloads == build_index_payloads(caps)


def test_lookup_ranking_and_filters(tmp_path: Path) -> None:
    store = KnowledgeStore(tmp_path / "knowledge")
    caps = _sample_caps()
    store.write_entry(
        _TARGET,
        KnowledgeEntry(
            manifest=_manifest(),
            capabilities=[cap.model_dump(mode="json") for cap in caps],
        ),
    )
    write_indexes(store, _TARGET, caps)
    index = KnowledgeIndex.open(store, _TARGET)

    assert index.capabilities  # lazy load works

    ranked = index.lookup(name="request")
    assert ranked[0].qualified_name == "fastapi.Request"
    assert ranked[1].name == "RequestBuilder"

    by_kind = index.lookup(kind=CapabilityKind.CLASS, limit=10)
    assert by_kind and all(cap.kind is CapabilityKind.CLASS for cap in by_kind)

    security = index.lookup(domain="security")
    assert [cap.name for cap in security] == ["HTTPBearer"]

    parent_id = next(cap.id for cap in caps if cap.name == "Request")
    children = index.lookup(parent=parent_id)
    assert [cap.name for cap in children] == ["get"]

    assert [cap.name for cap in index.lookup(query="bearer")] == ["HTTPBearer"]
    assert [cap.name for cap in index.lookup(deprecated=True)] == ["old_route"]
    assert all(not cap.deprecated for cap in index.lookup(limit=10) if cap.name != "old_route")

    only = next(cap for cap in caps if cap.name == "JSONResponse")
    assert [cap.name for cap in index.lookup(used_ids=[only.id])] == ["JSONResponse"]

    assert index.lookup(name="request", kind="class", limit=1)[0].name == "Request"


def test_missing_entry_is_empty_not_an_error(tmp_path: Path) -> None:
    index = KnowledgeIndex(tmp_path / "nothing")
    assert index.capabilities == []
    assert index.lookup(name="anything") == []
    assert index.lookup(query="bearer") == []


def test_lookup_limit_can_be_disabled(tmp_path: Path) -> None:
    store = KnowledgeStore(tmp_path / "knowledge")
    caps = _sample_caps()
    store.write_entry(
        _TARGET,
        KnowledgeEntry(
            manifest=_manifest(),
            capabilities=[cap.model_dump(mode="json") for cap in caps],
        ),
    )
    write_indexes(store, _TARGET, caps)
    index = KnowledgeIndex.open(store, _TARGET)
    assert len(index.lookup(limit=2)) == 2
    assert len(index.lookup(limit=0)) == len(caps)


def test_index_files_are_written(tmp_path: Path) -> None:
    store = KnowledgeStore(tmp_path / "knowledge")
    caps = _sample_caps()
    stats = write_indexes(store, _TARGET, caps)
    index_dir = store.entry_dir(_TARGET) / "index"
    assert stats["capabilities"] == len(caps)
    assert stats["tokens"] > 0
    assert set(stats["files"]) == {
        BY_NAME_FILENAME,
        BY_KIND_FILENAME,
        BY_DOMAIN_FILENAME,
        BY_PARENT_FILENAME,
        POSTINGS_FILENAME,
    }
    for filename in stats["files"]:
        assert (index_dir / filename).is_file()


def test_10k_capabilities_index_and_lookup_under_five_seconds(tmp_path: Path) -> None:
    caps = [
        _cap(
            f"cap{index:05d}",
            f"app.cap{index:05d}",
            description=(
                "http client utility number" if index % 100 == 0 else f"utility number {index}"
            ),
        )
        for index in range(10_000)
    ]
    store = KnowledgeStore(tmp_path / "knowledge")
    started = time.perf_counter()
    store.write_entry(
        _TARGET,
        KnowledgeEntry(
            manifest=_manifest(),
            capabilities=[cap.model_dump(mode="json") for cap in caps],
        ),
    )
    write_indexes(store, _TARGET, caps)
    elapsed = time.perf_counter() - started
    assert elapsed < 5.0, f"indexing 10k capabilities took {elapsed:.2f}s"

    index = KnowledgeIndex.open(store, _TARGET)
    assert index.lookup(name="cap05000")[0].name == "cap05000"
    assert index.lookup(kind="function", limit=3)
    assert index.lookup(domain="http")
    assert index.lookup(query="client")


def _manifest() -> KnowledgeManifest:
    return KnowledgeManifest(
        technology="fastapi",
        ecosystem="pypi",
        resolved_version="0.115.0",
        generated_at=datetime.now(UTC),
        tool_version="0.2.0",
    )
