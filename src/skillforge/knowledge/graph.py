"""Validated in-memory capability graph; no graph database required (K9)."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field

from skillforge.knowledge.store import KnowledgeStore
from skillforge.models import (
    Capability,
    CapabilityRelationshipKind,
    KnowledgeManifest,
    KnowledgeTarget,
)


@dataclass(frozen=True)
class GraphEdge:
    id: str
    source_id: str
    target_id: str
    kind: str


@dataclass(frozen=True)
class GraphStats:
    capabilities: int
    relationships: int
    domains: int


@dataclass
class CapabilityGraph:
    capabilities: dict[str, Capability]
    edges: list[GraphEdge] = field(default_factory=list)
    domains: dict[str, list[str]] = field(default_factory=dict)

    @property
    def stats(self) -> GraphStats:
        return GraphStats(len(self.capabilities), len(self.edges), len(self.domains))

    def children(self, parent_id: str) -> list[Capability]:
        return [
            self.capabilities[edge.source_id]
            for edge in self.edges
            if edge.target_id == parent_id and edge.kind == "member_of"
        ]

    def related(self, capability_id: str) -> list[Capability]:
        identifiers = {edge.target_id for edge in self.edges if edge.source_id == capability_id}
        identifiers.update(edge.source_id for edge in self.edges if edge.target_id == capability_id)
        return [self.capabilities[identifier] for identifier in sorted(identifiers)]

    def ancestors(self, capability_id: str) -> list[Capability]:
        result: list[Capability] = []
        current = self.capabilities.get(capability_id)
        seen: set[str] = set()
        while current is not None and current.parent_id:
            if current.parent_id in seen:
                raise ValueError("cyclic hierarchy")
            seen.add(current.parent_id)
            current = self.capabilities[current.parent_id]
            result.append(current)
        return result

    def domain_members(self, domain: str) -> list[Capability]:
        return [self.capabilities[identifier] for identifier in self.domains.get(domain, [])]


class GraphBuilder:
    def build(self, capabilities: list[Capability]) -> CapabilityGraph:
        nodes = {capability.id: capability for capability in capabilities}
        if len(nodes) != len(capabilities):
            raise ValueError("duplicate capability ids")
        edges: dict[tuple[str, str, str], GraphEdge] = {}
        domains: dict[str, list[str]] = {}
        hierarchy: dict[str, list[str]] = {}
        for capability in sorted(capabilities, key=lambda item: item.id):
            relationships = [(item.kind.value, item.target_id) for item in capability.relationships]
            if capability.parent_id:
                relationships.append(
                    (CapabilityRelationshipKind.MEMBER_OF.value, capability.parent_id)
                )
            for kind, target in relationships:
                if target not in nodes:
                    raise ValueError(f"unknown relationship target: {target}")
                key = (capability.id, target, kind)
                digest = hashlib.sha256(":".join(key).encode()).hexdigest()[:16]
                edges[key] = GraphEdge(f"rel-{digest}", capability.id, target, kind)
                if kind in {"member_of", "extends", "requires"}:
                    hierarchy.setdefault(capability.id, []).append(target)
            for domain in sorted(set(capability.domains)):
                domains.setdefault(domain, []).append(capability.id)
        self._check_cycles(hierarchy)
        return CapabilityGraph(
            nodes, [edges[key] for key in sorted(edges)], dict(sorted(domains.items()))
        )

    @staticmethod
    def _check_cycles(adjacency: dict[str, list[str]]) -> None:
        # Iterative DFS: a 20k-symbol hierarchy must not hit Python recursion limits.
        complete: set[str] = set()
        for root in adjacency:
            active: set[str] = set()
            stack = [(root, False)]
            while stack:
                node, leaving = stack.pop()
                if leaving:
                    active.discard(node)
                    complete.add(node)
                elif node in active:
                    raise ValueError("cyclic capability hierarchy")
                elif node not in complete:
                    active.add(node)
                    stack.append((node, True))
                    stack.extend((child, False) for child in reversed(adjacency.get(node, [])))


def write_graph(
    store: KnowledgeStore, target: KnowledgeTarget, graph: CapabilityGraph
) -> KnowledgeManifest:
    """Persist edges/domains using the store's hash-aware writer, never raw I/O."""
    # Rebuild to validate even if a caller constructed CapabilityGraph directly.
    validated = GraphBuilder().build(list(graph.capabilities.values()))
    entry = store.read_entry(target)
    if entry is None:
        raise ValueError("write the capability entry before persisting its graph")
    stored_ids = {item.get("id") for item in entry.capabilities}
    if stored_ids != set(validated.capabilities):
        raise ValueError("graph does not match the stored capability set")
    entry.relationships = [asdict(edge) for edge in validated.edges]
    entry.domains = dict(validated.domains)
    return store.write_entry(target, entry)
