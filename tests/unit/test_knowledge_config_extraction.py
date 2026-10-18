"""K10 config extraction: explicit defaults, bounded refs, no evaluation."""

import json
from pathlib import Path

from skillforge.knowledge.extractors.config import ConfigReferenceExtractor
from skillforge.knowledge.normalize import normalize_document
from skillforge.models import (
    KnowledgeSource,
    KnowledgeTarget,
    SourceAuthority,
    SourceVerification,
    SourceVerificationKind,
    VersionMatchKind,
)


def identity():
    target = KnowledgeTarget(ecosystem="pypi", technology="ruff", version="0.6.9")
    source = KnowledgeSource(
        ecosystem=target.ecosystem,
        id="schema-fixture",
        technology="ruff",
        version="0.6.9",
        url="https://docs.example.com/ruff/0.6.9/schema.json",
        authority=SourceAuthority.EXACT_ARTIFACT,
        version_match=VersionMatchKind.EXACT,
        verification=[SourceVerification(kind=SourceVerificationKind.ARTIFACT_EMBEDDED)],
    )
    return target, source


def extract(schema, **kwargs):
    target, source = identity()
    return ConfigReferenceExtractor().extract_schema(schema, source, target, **kwargs)


def test_schema_defaults_preserve_false_empty_and_null():
    result = extract(
        {
            "properties": {
                "boolean": {"type": "boolean", "default": False},
                "empty": {"type": "string", "default": ""},
                "nullable": {"type": "null", "default": None},
                "unknown": {"type": "string"},
            }
        }
    )
    by_name = {item.name: item.config for item in result.drafts}
    assert by_name["boolean"].default is False and by_name["boolean"].default_known
    assert by_name["empty"].default == "" and by_name["empty"].default_known
    assert by_name["nullable"].default is None and by_name["nullable"].default_known
    assert by_name["unknown"].default is None and not by_name["unknown"].default_known


def test_schema_nested_paths_required_choices_and_local_refs():
    result = extract(
        {
            "required": ["lint"],
            "properties": {
                "lint": {
                    "type": "object",
                    "properties": {
                        "mode": {"$ref": "#/$defs/mode"},
                    },
                }
            },
            "$defs": {"mode": {"type": "string", "enum": ["a", "b"], "default": "a"}},
        },
        file_format="toml",
    )
    records = {item.qualified_name: item for item in result.drafts}
    assert records["config:lint"].config.required is True
    mode = records["config:lint.mode"]
    assert mode.parent_qualified == "config:lint"
    assert mode.config.choices == ["a", "b"] and mode.config.value_type == "string"
    assert mode.config.required is False
    assert mode.tool_provenance[0].locator == "#/properties/lint/properties/mode"


def test_ruff_schema_fixture():
    path = Path(__file__).parents[1] / "fixtures/tool-intelligence/ruff/0.6.9/schema.json"
    result = extract(json.loads(path.read_text(encoding="utf-8")))
    select = next(item for item in result.drafts if item.name == "select")
    assert select.config.value_type == "array[string]"
    assert select.config.default == ["E4", "E7", "E9", "F"]


def test_remote_refs_are_not_fetched():
    result = extract({"properties": {"escape": {"$ref": "https://127.0.0.1/schema"}}})
    assert not result.drafts
    assert any("remote" in message for message in result.diagnostics)


def test_cycles_depth_and_node_budgets():
    result = extract(
        {
            "properties": {"loop": {"$ref": "#/$defs/loop"}},
            "$defs": {"loop": {"$ref": "#/$defs/loop"}},
        }
    )
    assert not result.drafts and any("cyclic" in message for message in result.diagnostics)
    result = extract(
        {"properties": {f"key{i}": {"type": "boolean"} for i in range(20)}}, max_capabilities=2
    )
    assert len(result.drafts) == 2
    nested = {"properties": {"child": {"properties": {"tooDeep": {"type": "string"}}}}}
    result = extract(nested, max_depth=1)
    assert len(result.drafts) == 1 and result.diagnostics


def test_unknown_default_type_and_no_inference_from_example():
    target, source = identity()
    doc = normalize_document(
        "# Example\n```toml\nfictional = true\n```\n"
        "## Configuration TOML lint.mode\nDefault: platform dependent\nA mode.\n",
        content_type="text/markdown",
        source_id=source.id,
        url=source.url,
        version_match=VersionMatchKind.EXACT,
    )
    result = ConfigReferenceExtractor().extract_reference(doc, source, target)
    mode = next(item for item in result.drafts if item.name == "mode")
    assert (
        mode.config.value_type is None
        and not mode.config.default_known
        and mode.config.required is None
    )
    assert "fictional" not in {item.name for item in result.drafts}
    assert result.diagnostics


def test_defaults_and_nested_secrets_redacted():
    result = extract(
        {
            "properties": {
                "api_token": {"type": "string", "default": "synthetic-value"},
                "headers": {"type": "object", "default": {"password": "synthetic-value"}},
            }
        }
    )
    assert "synthetic-value" not in "".join(item.model_dump_json() for item in result.drafts)


def test_sensitive_null_and_empty_defaults_remain_known():
    result = extract(
        {
            "properties": {
                "api_token": {"default": None},
                "password": {"default": ""},
                "auth_enabled": {"default": False},
            }
        }
    )
    by_name = {item.name: item.config for item in result.drafts}
    assert by_name["api_token"].default is None and by_name["api_token"].default_known
    assert by_name["password"].default == "" and by_name["password"].default_known
    assert by_name["auth_enabled"].default is False


def test_oversized_schema_and_document_are_gaps():
    assert extract({"description": "x" * (2 * 1024 * 1024 + 1)}).diagnostics
    target, source = identity()
    document = normalize_document(
        "x" * (2 * 1024 * 1024 + 1),
        content_type="text/plain",
        source_id=source.id,
        url=source.url,
        version_match=VersionMatchKind.EXACT,
    )
    result = ConfigReferenceExtractor().extract_reference(document, source, target)
    assert not result.drafts and result.diagnostics


def test_conditional_requiredness_is_unknown():
    result = extract(
        {
            "properties": {"mode": {"type": "string"}},
            "if": {"properties": {"other": {"const": True}}},
            "then": {"required": ["mode"]},
        }
    )
    assert result.drafts[0].config.required is None
    assert result.diagnostics


def test_schema_unknown_source_and_latest_refused():
    target, source = identity()
    source.version_match = VersionMatchKind.LATEST_ONLY
    result = ConfigReferenceExtractor().extract_schema({"properties": {"fake": {}}}, source, target)
    assert not result.drafts and result.diagnostics


def test_complex_branches_record_gaps_not_invented_choices():
    result = extract({"properties": {"mode": {"oneOf": [{"const": "a"}, {"const": "b"}]}}})
    assert result.drafts[0].config.choices is None
    assert result.drafts[0].config.value_type is None
    assert result.diagnostics


def test_json_pointer_escaping_and_invalid_schema():
    result = extract(
        {"properties": {"mode": {"$ref": "#/$defs/a~1b"}}, "$defs": {"a/b": {"type": "string"}}}
    )
    assert result.drafts[0].config.value_type == "string"
    assert extract(["not", "schema"]).diagnostics
    recursive = {}
    recursive["properties"] = {"loop": recursive}
    assert extract(recursive).diagnostics
