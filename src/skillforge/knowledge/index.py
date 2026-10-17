"""Deterministic local index over stored capabilities (K5).

Files written under ``<entry>/index/``:

* ``by-name.json`` — lowercased name *and* qualified name -> capability ids
* ``by-kind.json`` — kind -> ids
* ``by-domain.json`` — domain id -> ids
* ``by-parent.json`` — parent id -> child ids
* ``postings.json`` — search token -> ids (capped per token)

All maps are built from the sorted capability set, every bucket is sorted, and
lookups are lazy: index files load on first use and the capability list loads
only when a lookup needs record fields. No embeddings, no I/O beyond the entry.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from skillforge.knowledge.store import KnowledgeStore
from skillforge.knowledge.taxonomy import tokenize_for_search
from skillforge.models import Capability, CapabilityKind, KnowledgeTarget
from skillforge.utils.fs import atomic_write_text

INDEX_DIRNAME = "index"
BY_NAME_FILENAME = "by-name.json"
BY_KIND_FILENAME = "by-kind.json"
BY_DOMAIN_FILENAME = "by-domain.json"
BY_PARENT_FILENAME = "by-parent.json"
POSTINGS_FILENAME = "postings.json"

INDEX_FILENAMES: tuple[str, ...] = (
    BY_NAME_FILENAME,
    BY_KIND_FILENAME,
    BY_DOMAIN_FILENAME,
    BY_PARENT_FILENAME,
    POSTINGS_FILENAME,
)

#: One token's posting list never grows beyond this (most-relevant order is
#: capability-id order, deterministic).
POSTINGS_PER_TOKEN_CAP = 512

DEFAULT_LOOKUP_LIMIT = 50


def _add(mapping: dict[str, list[str]], key: str, value: str) -> None:
    bucket = mapping.setdefault(key, [])
    if value not in bucket:
        bucket.append(value)


def build_index_payloads(capabilities: Iterable[Capability]) -> dict[str, dict[str, list[str]]]:
    """Pure, deterministic index build (also the performance-tested path)."""
    by_name: dict[str, list[str]] = {}
    by_kind: dict[str, list[str]] = {}
    by_domain: dict[str, list[str]] = {}
    by_parent: dict[str, list[str]] = {}
    postings: dict[str, list[str]] = {}
    ordered = sorted(capabilities, key=lambda item: item.id)
    for capability in ordered:
        _add(by_name, capability.name.lower(), capability.id)
        if capability.qualified_name:
            _add(by_name, capability.qualified_name.lower(), capability.id)
        _add(by_kind, capability.kind.value, capability.id)
        for domain in capability.domains:
            _add(by_domain, domain, capability.id)
        if capability.parent_id:
            _add(by_parent, capability.parent_id, capability.id)
        searchable = " ".join(
            (
                capability.name,
                capability.qualified_name,
                capability.kind_detail,
                capability.description[:240],
            )
        )
        for token in tokenize_for_search(searchable):
            bucket = postings.setdefault(token, [])
            if len(bucket) < POSTINGS_PER_TOKEN_CAP:
                bucket.append(capability.id)
    for mapping in (by_name, by_kind, by_domain, by_parent, postings):
        for key, bucket in mapping.items():
            mapping[key] = sorted(bucket)
    return {
        "by_name": dict(sorted(by_name.items())),
        "by_kind": dict(sorted(by_kind.items())),
        "by_domain": dict(sorted(by_domain.items())),
        "by_parent": dict(sorted(by_parent.items())),
        "postings": dict(sorted(postings.items())),
    }


def write_indexes(
    store: KnowledgeStore,
    target: KnowledgeTarget,
    capabilities: Sequence[Capability],
) -> dict[str, Any]:
    """Build and atomically write the index files for one store entry."""
    payloads = build_index_payloads(capabilities)
    index_dir = store.entry_dir(target) / INDEX_DIRNAME
    files = {
        BY_NAME_FILENAME: payloads["by_name"],
        BY_KIND_FILENAME: payloads["by_kind"],
        BY_DOMAIN_FILENAME: payloads["by_domain"],
        BY_PARENT_FILENAME: payloads["by_parent"],
        POSTINGS_FILENAME: payloads["postings"],
    }
    for filename, payload in files.items():
        atomic_write_text(
            index_dir / filename,
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
        )
    return {
        "capabilities": len({capability.id for capability in capabilities}),
        "tokens": len(payloads["postings"]),
        "files": sorted(files),
    }


class KnowledgeIndex:
    """Lazy reader for the structured index of one store entry."""

    def __init__(self, entry_dir: Path) -> None:
        self.entry_dir = Path(entry_dir)
        self._capabilities: list[Capability] | None = None
        self._cache: dict[str, dict[str, list[str]]] = {}

    @classmethod
    def open(cls, store: KnowledgeStore, target: KnowledgeTarget) -> KnowledgeIndex:
        return cls(store.entry_dir(target))

    # ------------------------------------------------------------------ data
    @property
    def capabilities(self) -> list[Capability]:
        if self._capabilities is None:
            self._capabilities = self._read_capabilities()
        return self._capabilities

    def _read_capabilities(self) -> list[Capability]:
        path = self.entry_dir / "capabilities.jsonl"
        if not path.is_file():
            return []
        records: list[Capability] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                records.append(Capability.model_validate_json(line))
            except ValidationError:  # malformed line: skip, keep the rest usable
                continue
        return records

    def _load(self, filename: str) -> dict[str, list[str]]:
        if filename in self._cache:
            return self._cache[filename]
        path = self.entry_dir / INDEX_DIRNAME / filename
        payload: dict[str, list[str]] = {}
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = None
            if isinstance(data, dict):
                payload = {
                    str(key): [str(item) for item in value]
                    for key, value in data.items()
                    if isinstance(value, list)
                }
        self._cache[filename] = payload
        return payload

    # --------------------------------------------------------------- lookup
    def lookup(
        self,
        *,
        name: str | None = None,
        qualified: str | None = None,
        kind: CapabilityKind | str | None = None,
        domain: str | None = None,
        parent: str | None = None,
        deprecated: bool | None = None,
        used_ids: Iterable[str] | None = None,
        query: str | None = None,
        limit: int = DEFAULT_LOOKUP_LIMIT,
    ) -> list[Capability]:
        filters: list[set[str]] = []
        if name:
            filters.append(self._name_candidates(name))
        if qualified:
            filters.append(self._name_candidates(qualified))
        if kind is not None:
            kind_value = kind.value if isinstance(kind, CapabilityKind) else str(kind)
            filters.append(set(self._load(BY_KIND_FILENAME).get(kind_value, ())))
        if domain:
            filters.append(set(self._load(BY_DOMAIN_FILENAME).get(domain, ())))
        if parent:
            filters.append(set(self._load(BY_PARENT_FILENAME).get(parent, ())))
        if query:
            filters.append(self._query_candidates(query))
        if used_ids is not None:
            filters.append(set(used_ids))

        candidates: set[str] | None = None
        for bucket in filters:
            candidates = bucket if candidates is None else candidates & bucket

        records: list[Capability] = self.capabilities
        if candidates is not None:
            records = [record for record in records if record.id in candidates]
        if deprecated is not None:
            records = [record for record in records if bool(record.deprecated) is deprecated]
        ranked = sorted(
            records,
            key=lambda item: self._rank_key(item, name=name, qualified=qualified, query=query),
        )
        return ranked if limit <= 0 else ranked[:limit]

    def _name_candidates(self, value: str) -> set[str]:
        """Exact name hits plus token postings, so prefix/suffix ranking works."""
        candidates = set(self._load(BY_NAME_FILENAME).get(value.lower(), ()))
        postings = self._load(POSTINGS_FILENAME)
        for token in tokenize_for_search(value):
            candidates.update(postings.get(token, ()))
        return candidates

    def _query_candidates(self, query: str) -> set[str]:
        postings = self._load(POSTINGS_FILENAME)
        candidates: set[str] = set()
        for token in tokenize_for_search(query):
            candidates.update(postings.get(token, ()))
        return candidates

    @staticmethod
    def _rank_key(
        capability: Capability,
        *,
        name: str | None,
        qualified: str | None,
        query: str | None,
    ) -> tuple[int, int, str, str]:
        score = 0
        if name:
            target = name.lower()
            lowered_name = capability.name.lower()
            lowered_qualified = capability.qualified_name.lower()
            if lowered_name == target:
                score += 100
            elif lowered_qualified == target:
                score += 90
            elif lowered_qualified.endswith("." + target):
                score += 80
            elif lowered_name.startswith(target) or lowered_qualified.startswith(target):
                score += 60
        if qualified:
            target = qualified.lower()
            if capability.qualified_name.lower() == target:
                score += 90
            elif capability.name.lower() == target:
                score += 80
        if query:
            query_tokens = set(tokenize_for_search(query))
            capability_tokens = set(
                tokenize_for_search(f"{capability.name} {capability.qualified_name}")
            )
            score += 10 * len(query_tokens & capability_tokens)
        return (-score, capability.kind.rank, capability.name, capability.id)


__all__ = [
    "BY_DOMAIN_FILENAME",
    "BY_KIND_FILENAME",
    "BY_NAME_FILENAME",
    "BY_PARENT_FILENAME",
    "DEFAULT_LOOKUP_LIMIT",
    "INDEX_DIRNAME",
    "INDEX_FILENAMES",
    "POSTINGS_FILENAME",
    "POSTINGS_PER_TOKEN_CAP",
    "KnowledgeIndex",
    "build_index_payloads",
    "write_indexes",
]
