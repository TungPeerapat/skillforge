"""K8/K9 proof guards, merge precedence, graph validation and navigation."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from skillforge.knowledge.extractors import ExtractionPlan, extract_capabilities
from skillforge.knowledge.graph import GraphBuilder, write_graph
from skillforge.knowledge.index import KnowledgeIndex, write_indexes
from skillforge.knowledge.introspect import ArtifactLocation, introspect_artifact
from skillforge.knowledge.normalize import normalize_document
from skillforge.knowledge.store import KnowledgeEntry, KnowledgeStore
from skillforge.models import (
    ArtifactEvidence,
    Capability,
    CapabilityDraft,
    CapabilityKind,
    CapabilityParameter,
    CapabilityRelationship,
    CapabilityRelationshipKind,
    CapabilitySignature,
    Certainty,
    KnowledgeManifest,
    KnowledgeSource,
    KnowledgeTarget,
    SourceAuthority,
    SourceVerification,
    SourceVerificationKind,
    VersionMatchKind,
)


def draft(**kwargs) -> CapabilityDraft:
    values = {
        "technology": "demo",
        "ecosystem": "pypi",
        "version": "1.0.0",
        "kind": CapabilityKind.FUNCTION,
        "name": "fetch",
        "qualified_name": "demo.fetch",
        "confidence": Certainty.FACT,
        "evidence": [ArtifactEvidence(kind="ast_export", path="demo/api.py", locator="L1")],
    }
    values.update(kwargs)
    return CapabilityDraft(**values)


def plan() -> ExtractionPlan:
    return ExtractionPlan(KnowledgeTarget(ecosystem="pypi", technology="demo", version="1.0.0"))


def source() -> KnowledgeSource:
    return KnowledgeSource(
        id="src-docs",
        technology="demo",
        version="1.0.0",
        url="https://docs.example.com/1.0.0/",
        authority=SourceAuthority.EXACT_DOCS,
        version_match=VersionMatchKind.EXACT,
        verification=[SourceVerification(kind=SourceVerificationKind.REGISTRY_DOMAIN_MAP)],
    )


def test_document_examples_do_not_invent_api() -> None:
    src = source()
    document = normalize_document(
        "# Getting started\n```python\ndef fictional_api(): pass\n```\n"
        "## function demo.fetch\nFetch a resource.\n",
        content_type="text/markdown",
        source_id=src.id,
        url=src.url,
        version_match=VersionMatchKind.EXACT,
    )
    result = extract_capabilities(plan(), documents=[document], sources={src.id: src})
    assert [item.qualified_name for item in result.capabilities] == ["demo.fetch"]
    assert result.capabilities[0].sources == [src.id]


def test_latest_docs_cannot_back_capabilities() -> None:
    src = source().model_copy(update={"version_match": VersionMatchKind.LATEST_ONLY})
    document = normalize_document(
        "## function demo.fetch\n",
        content_type="text/markdown",
        source_id=src.id,
        url=src.url,
        version_match=VersionMatchKind.LATEST_ONLY,
    )
    assert not extract_capabilities(
        plan(), documents=[document], sources={src.id: src}
    ).capabilities


def test_artifact_signature_precedence_and_conflicts() -> None:
    first = draft(signature=CapabilitySignature(parameters=[CapabilityParameter(name="url")]))
    second = draft(signature=CapabilitySignature(parameters=[CapabilityParameter(name="other")]))
    result = extract_capabilities(plan(), artifacts=[first, second])
    assert len(result.capabilities) == 1
    assert result.capabilities[0].signature.parameters[0].name == "url"
    assert result.conflicts[0]["kind"] == "signature"


def test_unproven_or_wrong_version_is_excluded() -> None:
    result = extract_capabilities(plan(), artifacts=[draft(evidence=[]), draft(version="2.0.0")])
    assert result.capabilities == []
    assert len(result.diagnostics) == 2


def test_quotes_bounded_and_inputs_not_mutated() -> None:
    original = draft(description="x" * 500)
    result = extract_capabilities(plan(), artifacts=[original])
    assert len(result.capabilities[0].description) == 200
    assert len(original.description) == 500


def cap(identifier: str, parent: str | None = None) -> Capability:
    return Capability(
        id=identifier,
        technology="demo",
        ecosystem="pypi",
        version="1.0.0",
        kind=CapabilityKind.CLASS,
        name=identifier,
        qualified_name=identifier,
        parent_id=parent,
        domains=["http"],
    )


def test_graph_navigation_and_determinism() -> None:
    nodes = [cap("root"), cap("child", "root"), cap("leaf", "child")]
    graph = GraphBuilder().build(nodes)
    assert [item.id for item in graph.children("root")] == ["child"]
    assert [item.id for item in graph.ancestors("leaf")] == ["child", "root"]
    assert {item.id for item in graph.domain_members("http")} == {"root", "child", "leaf"}
    assert graph.edges == GraphBuilder().build(list(reversed(nodes))).edges
    assert graph.stats.relationships == 2


@pytest.mark.parametrize("nodes", [[cap("a", "b"), cap("b", "a")], [cap("a", "a")]])
def test_cycles_rejected(nodes) -> None:
    with pytest.raises(ValueError, match="cyclic"):
        GraphBuilder().build(nodes)


def test_unknown_target_and_duplicate_ids_rejected() -> None:
    with pytest.raises(ValueError, match="unknown"):
        GraphBuilder().build([cap("a", "missing")])
    with pytest.raises(ValueError, match="duplicate"):
        GraphBuilder().build([cap("a"), cap("a")])


def test_related_edges_need_not_form_a_dag() -> None:
    first, second = cap("a"), cap("b")
    first.relationships = [
        CapabilityRelationship(kind=CapabilityRelationshipKind.RELATED_TO, target_id="b")
    ]
    second.relationships = [
        CapabilityRelationship(kind=CapabilityRelationshipKind.RELATED_TO, target_id="a")
    ]
    graph = GraphBuilder().build([first, second])
    assert [item.id for item in graph.related("a")] == ["b"]


def test_deep_graph_does_not_recurse() -> None:
    nodes = [cap(str(index), str(index - 1) if index else None) for index in range(2000)]
    graph = GraphBuilder().build(nodes)
    assert len(graph.ancestors("1999")) == 1999


def test_graph_persistence_updates_manifest_hashes(tmp_path) -> None:
    store = KnowledgeStore(tmp_path)
    target = plan().target
    nodes = [cap("root"), cap("child", "root")]
    store.write_entry(
        target,
        KnowledgeEntry(
            manifest=KnowledgeManifest(
                technology="demo",
                ecosystem="pypi",
                resolved_version="1.0.0",
                generated_at=datetime.now(UTC),
            ),
            capabilities=[node.model_dump(mode="json") for node in nodes],
        ),
    )
    manifest = write_graph(store, target, GraphBuilder().build(nodes))
    assert manifest.domain_count == 1
    assert store.validate_entry(target).ok
    assert len(store.read_relationships(target)) == 1
    assert store.read_json_file(target, "domains.json") == {"http": ["child", "root"]}


def test_artifact_to_store_index_and_graph_offline(tmp_path) -> None:
    package = tmp_path / "demo"
    package.mkdir()
    (package / "__init__.py").write_text(
        "class Client:\n    def fetch(self, url: str) -> str: ...\n",
        encoding="utf-8",
    )
    drafts = introspect_artifact(ArtifactLocation("pypi", "demo", "1.0.0", package)).drafts
    result = extract_capabilities(plan(), artifacts=drafts)
    assert {item.qualified_name for item in result.capabilities} == {
        "demo.Client",
        "demo.Client.fetch",
    }
    store = KnowledgeStore(tmp_path / "knowledge")
    target = plan().target
    store.write_entry(
        target,
        KnowledgeEntry(
            manifest=KnowledgeManifest(
                technology="demo",
                ecosystem="pypi",
                resolved_version="1.0.0",
                generated_at=datetime.now(UTC),
            ),
            capabilities=[item.model_dump(mode="json") for item in result.capabilities],
        ),
    )
    graph = GraphBuilder().build(result.capabilities)
    write_graph(store, target, graph)
    write_indexes(store, target, result.capabilities)
    index = KnowledgeIndex.open(store, target)
    client = index.lookup(name="Client")[0]
    assert [item.name for item in graph.children(client.id)] == ["fetch"]
    assert index.lookup(name="fetch")[0].signature.parameters[0].name == "url"
    assert store.validate_entry(target).ok
