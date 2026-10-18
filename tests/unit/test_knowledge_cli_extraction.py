"""K10 CLI contracts: offline reference parsing, provenance and integration."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from skillforge.knowledge.extractors import ExtractionPlan, extract_capabilities
from skillforge.knowledge.extractors.cli import CliReferenceExtractor
from skillforge.knowledge.graph import GraphBuilder
from skillforge.knowledge.index import KnowledgeIndex, write_indexes
from skillforge.knowledge.normalize import normalize_document
from skillforge.knowledge.store import KnowledgeEntry, KnowledgeStore
from skillforge.models import (
    Capability,
    CapabilityKind,
    KnowledgeManifest,
    KnowledgeSource,
    KnowledgeTarget,
    SourceAuthority,
    SourceVerification,
    SourceVerificationKind,
    VersionMatchKind,
)


def reference(tool="ruff", version="0.6.9", text=None):
    target = KnowledgeTarget(
        ecosystem={"pytest": "pypi", "ruff": "pypi", "next": "npm", "dotnet": "tool"}[tool],
        technology=tool,
        version=version,
    )
    source = KnowledgeSource(
        id=f"fixture-{tool}-{version}",
        ecosystem=target.ecosystem,
        technology=tool,
        version=version,
        url=f"https://docs.example.com/{tool}/{version}/",
        authority=SourceAuthority.EXACT_DOCS,
        version_match=VersionMatchKind.EXACT,
        verification=[
            SourceVerification(
                kind=SourceVerificationKind.REGISTRY_DOMAIN_MAP, detail="synthetic test authority"
            )
        ],
    )
    if text is None:
        text = (
            Path(__file__).parents[1]
            / "fixtures"
            / "tool-intelligence"
            / tool
            / version
            / "reference.md"
        ).read_text(encoding="utf-8")
    document = normalize_document(
        text,
        content_type="text/markdown",
        source_id=source.id,
        url=source.url,
        version_match=VersionMatchKind.EXACT,
    )
    return target, source, document


@pytest.mark.parametrize(
    "tool,version,command,key",
    [
        ("pytest", "8.2.2", "cli:pytest", "config:tool.pytest.ini_options.addopts"),
        ("ruff", "0.6.9", "cli:ruff check", "config:lint.select"),
        ("next", "16.0.1", "cli:next build", "config:reactStrictMode"),
        ("dotnet", "8.0.100", "cli:dotnet build", "config:RestoreSources"),
    ],
)
def test_four_tool_references_expose_cli_and_config(tool, version, command, key):
    target, source, document = reference(tool, version)
    result = extract_capabilities(
        ExtractionPlan(target), documents=[document], sources={source.id: source}
    )
    records = {item.qualified_name: item for item in result.capabilities}
    assert command in records and key in records
    assert records[command].cli.command_path == command.removeprefix("cli:").split()
    assert all(item.sources == [source.id] and item.tool_provenance for item in records.values())
    assert all(item.version == version for item in records.values())
    assert GraphBuilder().build(result.capabilities).stats.relationships > 0


def test_flag_aliases_and_unknown_defaults():
    target, source, document = reference()
    records = {
        item.name: item
        for item in CliReferenceExtractor().extract_reference(document, source, target).drafts
    }
    assert records["--quiet"].option.names == ["-q", "--quiet"]
    assert records["--quiet"].option.default_known is False
    assert records["--quiet"].option.required is None
    assert records["--fix"].option.default_known is True
    assert records["--fix"].option.default is False
    assert records["--output-format"].option.takes_value is True
    assert records["--output-format"].option.value_name == "FORMAT"
    assert records["--output-format"].option.value_type is None
    assert records["--output-format"].option.default == "text"


@pytest.mark.parametrize(
    "match", [VersionMatchKind.LATEST_ONLY, VersionMatchKind.MISMATCH, VersionMatchKind.COMPATIBLE]
)
def test_nonexact_versions_are_refused(match):
    target, source, document = reference()
    source.version_match = match
    assert not CliReferenceExtractor().extract_reference(document, source, target).drafts


def test_wrong_identity_url_and_version_are_refused():
    target, source, document = reference()
    for changes in (
        {"technology": "pytest"},
        {"version": "1.0.0"},
        {"id": "wrong"},
        {"url": "https://other.example/"},
    ):
        assert (
            not CliReferenceExtractor()
            .extract_reference(document, source.model_copy(update=changes), target)
            .drafts
        )


def test_versions_do_not_leak_into_each_other():
    old_t, old_s, old_doc = reference()
    new_t, new_s, new_doc = reference(version="0.7.0")
    old = extract_capabilities(
        ExtractionPlan(old_t),
        documents=[old_doc, new_doc],
        sources={old_s.id: old_s, new_s.id: new_s},
    )
    new = extract_capabilities(
        ExtractionPlan(new_t), documents=[new_doc], sources={new_s.id: new_s}
    )
    assert "--new-fixture-flag" not in {item.name for item in old.capabilities}
    assert "--new-fixture-flag" in {item.name for item in new.capabilities}
    assert {item.id for item in old.capabilities}.isdisjoint(item.id for item in new.capabilities)


def test_ansi_wrapping_and_subcommands():
    target, source, document = reference(
        text='# CLI reference\n```text\nUsage: ruff [OPTIONS]\nCommands:\n  check, lint  Check source.\nOptions:\n  \x1b[31m--color MODE\x1b[0m  Color policy.\n                [default: "auto"]\n```\n'
    )
    records = {
        item.name: item
        for item in CliReferenceExtractor().extract_reference(document, source, target).drafts
    }
    assert records["check"].cli.aliases == ["ruff lint"]
    assert records["--color"].option.default == "auto"
    assert "\x1b" not in records["--color"].model_dump_json()


def test_examples_and_unparsed_syntax_do_not_become_api():
    target, source, document = reference(
        text="# Examples\nUsage: ruff imaginary\nOptions:\n --fictional  Example only.\n"
    )
    assert not CliReferenceExtractor().extract_reference(document, source, target).drafts
    target, source, document = reference(
        text="# CLI reference\n```\nUsage: ruff\nOptions:\n --choice {a,b}  Unsupported grammar.\n```\n"
    )
    result = CliReferenceExtractor().extract_reference(document, source, target)
    assert "--choice" not in {item.name for item in result.drafts}
    assert result.diagnostics


def test_default_secrets_are_redacted_and_budgets_apply():
    target, source, document = reference(
        text='# CLI reference\n```\nUsage: ruff\nOptions:\n --token VALUE  Credential. [default: "not-a-real-secret"]\n```\n'
    )
    result = CliReferenceExtractor().extract_reference(document, source, target)
    assert "not-a-real-secret" not in "".join(item.model_dump_json() for item in result.drafts)
    result = CliReferenceExtractor().extract_reference(document, source, target, max_capabilities=1)
    assert len(result.drafts) == 1 and result.diagnostics


def test_index_graph_store_roundtrip_and_alias_lookup(tmp_path):
    target, source, document = reference()
    result = extract_capabilities(
        ExtractionPlan(target), documents=[document], sources={source.id: source}
    )
    store = KnowledgeStore(tmp_path)
    store.write_entry(
        target,
        KnowledgeEntry(
            manifest=KnowledgeManifest(
                technology=target.technology,
                ecosystem=target.ecosystem,
                resolved_version=target.version,
                generated_at=datetime.now(UTC),
            ),
            capabilities=[item.model_dump(mode="json") for item in result.capabilities],
        ),
    )
    write_indexes(store, target, result.capabilities)
    index = KnowledgeIndex.open(store, target)
    quiet = index.lookup(name="-q")[0]
    assert quiet.option.names == ["-q", "--quiet"]
    assert "cli" in quiet.domains
    assert index.lookup(kind=CapabilityKind.CONFIG_KEY)
    graph = GraphBuilder().build(index.capabilities)
    command = index.lookup(qualified="cli:ruff check")[0]
    assert "--quiet" in {item.name for item in graph.children(command.id)}
    assert store.validate_entry(target).ok


def test_old_records_and_kind_ranks_are_unchanged():
    capability = Capability(
        id="old",
        technology="demo",
        ecosystem="pypi",
        version="1.0.0",
        kind=CapabilityKind.FUNCTION,
        name="f",
        qualified_name="demo.f",
    )
    restored = Capability.model_validate_json(
        capability.model_dump_json(exclude={"cli", "option", "config", "tool_provenance"})
    )
    assert restored.option is None and restored.tool_provenance == []
    assert CapabilityKind.MIDDLEWARE.rank == 17
    assert CapabilityKind.CLI_SUBCOMMAND.rank == 18
    assert (
        Capability.make_id("pypi", "demo", "1.0.0", "demo.f", CapabilityKind.FUNCTION)
        == "cap-"
        + __import__("hashlib").sha256(b"pypi:demo@1.0.0:function:demo.f").hexdigest()[:12]
    )


def test_missing_or_cross_ecosystem_identity_cannot_back_cli():
    target, source, document = reference()
    for ecosystem in (None, "npm"):
        legacy = source.model_copy(update={"ecosystem": ecosystem})
        result = CliReferenceExtractor().extract_reference(document, legacy, target)
        assert not result.drafts and result.diagnostics


def test_arguments_have_only_explicit_usage_semantics():
    target, source, document = reference(
        text=(
            "# CLI reference\n```\nUsage: ruff [FILES...] <OUTPUT>\nArguments:\n"
            "  FILES   Input files.\n  OUTPUT   Output.\n  OTHER   Not specified by usage.\n```\n"
        )
    )
    result = CliReferenceExtractor().extract_reference(document, source, target)
    arguments = {item.name: item.argument for item in result.drafts if item.argument}
    assert arguments["FILES"].required is False and arguments["FILES"].repeatable is True
    assert arguments["OUTPUT"].required is True and arguments["OUTPUT"].repeatable is False
    assert arguments["OTHER"].required is None and arguments["OTHER"].repeatable is None


def test_offline_pipeline_never_calls_process_or_network(monkeypatch):
    import socket
    import subprocess

    def forbidden(*args, **kwargs):
        raise AssertionError("offline extraction attempted execution/network")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    target, source, document = reference()
    assert extract_capabilities(
        ExtractionPlan(target), documents=[document], sources={source.id: source}
    ).capabilities


def test_option_metadata_conflicts_are_preserved():
    target, source, document = reference()
    first = CliReferenceExtractor().extract_reference(document, source, target).drafts
    second = [item.model_copy(deep=True) for item in first]
    for item in second:
        if item.option and item.name == "--fix":
            item.option.default = True
    result = extract_capabilities(
        ExtractionPlan(target), artifacts=[*first, *second], sources={source.id: source}
    )
    assert any(conflict["kind"] == "option" for conflict in result.conflicts)
    assert (
        next(item for item in result.capabilities if item.name == "--fix").option.default is False
    )


def test_later_evidence_fills_unknown_metadata_without_conflict():
    target, source, document = reference()
    first = CliReferenceExtractor().extract_reference(document, source, target).drafts
    second = [item.model_copy(deep=True) for item in first]
    for item in second:
        if item.name == "--quiet":
            item.option.default_known = True
            item.option.default = False
    result = extract_capabilities(
        ExtractionPlan(target), artifacts=[*first, *second], sources={source.id: source}
    )
    quiet = next(item for item in result.capabilities if item.name == "--quiet")
    assert quiet.option.default_known and quiet.option.default is False
    assert not result.conflicts
    assert not next(item for item in first if item.name == "--quiet").option.default_known
