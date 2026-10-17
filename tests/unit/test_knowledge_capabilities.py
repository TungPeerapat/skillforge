"""Capability IR + taxonomy tests (K5)."""

from __future__ import annotations

from skillforge.knowledge.capabilities import (
    capability_kind_counts,
    normalize_capabilities,
    normalize_capability,
)
from skillforge.knowledge.taxonomy import (
    detect_domains,
    domain_ids,
    split_identifier,
    tokenize_for_search,
)
from skillforge.models import (
    Capability,
    CapabilityKind,
    CapabilityParameter,
    CapabilityRelationship,
    CapabilityRelationshipKind,
    CapabilitySignature,
)


def _cap(
    qualified: str = "app.Request.json",
    name: str = "json",
    kind: CapabilityKind = CapabilityKind.METHOD,
    **overrides: object,
) -> Capability:
    payload: dict[str, object] = {
        "id": "",
        "technology": "fastapi",
        "ecosystem": "pypi",
        "version": "0.115.0",
        "kind": kind,
        "name": name,
        "qualified_name": qualified,
    }
    payload.update(overrides)
    return Capability.model_validate(payload)


def test_capability_id_is_deterministic_and_version_specific() -> None:
    first = Capability.make_id(
        "pypi", "fastapi", "0.115.0", "app.Request.json", CapabilityKind.METHOD
    )
    second = Capability.make_id(
        "pypi", "fastapi", "0.115.0", "app.Request.json", CapabilityKind.METHOD
    )
    third = Capability.make_id(
        "pypi", "fastapi", "0.116.0", "app.Request.json", CapabilityKind.METHOD
    )
    assert first == second
    assert first.startswith("cap-") and len(first) == 16
    assert first != third


def test_normalize_fills_id_and_detects_domains() -> None:
    normalized = normalize_capability(_cap())
    assert normalized.id.startswith("cap-")
    assert normalized.name == "json"
    assert {"http", "serialization"}.issubset(set(normalized.domains))


def test_normalize_sorts_and_deduplicates_everything() -> None:
    relationship = CapabilityRelationship(
        kind=CapabilityRelationshipKind.RELATED_TO, target_id="cap-b"
    )
    capability = _cap(
        relationships=[
            relationship,
            CapabilityRelationship(kind=CapabilityRelationshipKind.RELATED_TO, target_id="cap-a"),
            CapabilityRelationship(kind=CapabilityRelationshipKind.RELATED_TO, target_id="cap-b"),
        ],
        sources=["src-2", "src-1", "src-1"],
        signature=CapabilitySignature(
            raises=["ValueError", "TypeError", "ValueError"],
            parameters=[CapabilityParameter(name="z"), CapabilityParameter(name="a")],
        ),
    )
    normalized = normalize_capability(capability)
    assert [item.target_id for item in normalized.relationships] == ["cap-a", "cap-b"]
    assert normalized.sources == ["src-1", "src-2"]
    assert normalized.signature is not None
    assert normalized.signature.raises == ["TypeError", "ValueError"]
    assert [item.name for item in normalized.signature.parameters] == ["z", "a"]


def test_normalize_capabilities_deduplicates_by_id_and_sorts() -> None:
    duplicate = _cap()
    other = _cap(qualified="app.Response", name="Response", kind=CapabilityKind.CLASS)
    result = normalize_capabilities([_cap(), duplicate, other])
    assert len(result) == 2
    assert [item.qualified_name for item in result] == ["app.Request.json", "app.Response"]


def test_kind_counts() -> None:
    caps = normalize_capabilities(
        [_cap(), _cap(qualified="app.Response", name="Response", kind=CapabilityKind.CLASS)]
    )
    assert capability_kind_counts(caps) == {"class": 1, "method": 1}


def test_tokenizer_handles_camel_snake_kebab_and_acronyms() -> None:
    assert split_identifier("HTTPBearer.get_token-value_2") == [
        "http",
        "bearer",
        "get",
        "token",
        "value",
    ]
    assert tokenize_for_search("JSONResponse") == ["json", "response"]


def test_detect_domains_keywords_and_kind_hints() -> None:
    assert "http" in detect_domains("JSONResponse")
    assert "serialization" in detect_domains("JSONResponse")
    assert "cli" in detect_domains("logout", kind=CapabilityKind.CLI_COMMAND)
    assert detect_domains("unrelated_thing") == []


def test_domain_ids_are_unique() -> None:
    ids = domain_ids()
    assert len(ids) == len(set(ids))
    assert "http" in ids and "security" in ids
