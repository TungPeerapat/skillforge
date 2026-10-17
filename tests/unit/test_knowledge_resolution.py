"""Exact version resolution tests (K2): ladder, conflicts, acceptance."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from skillforge.analyzer.profile import analyze_repository
from skillforge.analyzer.scanner import ScanOptions, ScanResult, scan_repository
from skillforge.config import Settings, load_settings
from skillforge.knowledge import build_inventory, detect_pin
from skillforge.knowledge.resolution import resolve_inventory, resolve_repository
from skillforge.models import (
    Certainty,
    Dependency,
    DependencyInventory,
    DependencyRelationship,
    Evidence,
    RepositoryProfile,
    ResolutionMethod,
)
from skillforge.models.knowledge import KnowledgePriority
from skillforge.models.repository import DependencyScope

_UV_LOCK = """\
[[package]]
name = "fastapi"
version = "0.115.0"

[[package]]
name = "sqlalchemy"
version = "2.0.35"

[[package]]
name = "anyio"
version = "4.4.0"
"""


def _settings(tmp_path: Path, **knowledge: object):
    overrides = {"knowledge": knowledge} if knowledge else None
    return load_settings(tmp_path, env={}, overrides=overrides)


def _dependency(
    name: str,
    spec: str | None,
    *,
    ecosystem: str = "pypi",
    scope: DependencyScope = DependencyScope.RUNTIME,
) -> Dependency:
    return Dependency(
        ecosystem=ecosystem,
        name=name,
        version_spec=spec,
        scope=scope,
        manifest="pyproject.toml",
        evidence=[Evidence(source="pyproject.toml", weight=0.7)],
    )


def _profile(dependencies: list[Dependency] | None = None) -> RepositoryProfile:
    return RepositoryProfile(
        generated_at=datetime.now(UTC),
        name="demo",
        root_path=".",
        dependencies=dependencies or [],
    )


def _scan(contents: dict[str, str]) -> ScanResult:
    return ScanResult(root=Path("."), options=ScanOptions(), contents=dict(contents))


def _inventory(dependencies: list[Dependency]) -> DependencyInventory:
    return build_inventory(_profile(dependencies), Settings())


def _write_metadata(root: Path, name: str, version: str) -> None:
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
    path.write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n", encoding="utf-8")


# ------------------------------------------------------------------- ladder


def test_lockfile_resolves_ranges_and_creates_transitive_entries(tmp_path: Path) -> None:
    inventory = _inventory([_dependency("fastapi", ">=0.115"), _dependency("sqlalchemy", ">=2.0")])
    result = resolve_inventory(
        inventory, root=tmp_path, scan=_scan({"uv.lock": _UV_LOCK}), settings=_settings(tmp_path)
    )
    fastapi = result.inventory.by_name("fastapi")
    assert fastapi is not None
    assert fastapi.version.resolved == "0.115.0"
    assert fastapi.version.method is ResolutionMethod.LOCKFILE
    assert fastapi.version.certainty is Certainty.FACT
    assert fastapi.lockfiles == ["uv.lock"]

    anyio = result.inventory.by_name("anyio")
    assert anyio is not None
    assert anyio.relationship is DependencyRelationship.TRANSITIVE
    assert anyio.importance is KnowledgePriority.TRANSITIVE

    targets = {(target.technology, target.version) for target in result.inventory.knowledge_targets}
    assert ("fastapi", "0.115.0") in targets
    assert ("anyio", "4.4.0") not in targets
    assert result.stats["resolved_lockfile"] == 2
    assert result.stats["transitive_entries"] == 1


def test_installed_artifact_resolves_without_lockfile(tmp_path: Path) -> None:
    _write_metadata(tmp_path, "fastapi", "0.115.0")
    inventory = _inventory([_dependency("fastapi", ">=0.115")])
    result = resolve_inventory(
        inventory, root=tmp_path, scan=_scan({}), settings=_settings(tmp_path)
    )
    fastapi = result.inventory.by_name("fastapi")
    assert fastapi is not None
    assert fastapi.version.resolved == "0.115.0"
    assert fastapi.version.method is ResolutionMethod.INSTALLED_ARTIFACT
    assert result.stats["resolved_installed_artifact"] == 1


def test_manifest_pin_resolves_without_lockfile(tmp_path: Path) -> None:
    inventory = _inventory([_dependency("fastapi", "==0.115.0")])
    result = resolve_inventory(
        inventory, root=tmp_path, scan=_scan({}), settings=_settings(tmp_path)
    )
    fastapi = result.inventory.by_name("fastapi")
    assert fastapi is not None
    assert fastapi.version.method is ResolutionMethod.MANIFEST_PIN
    assert fastapi.version.is_resolved
    assert result.stats["resolved_manifest_pin"] == 1


# ----------------------------------------------------------------- conflicts


def test_lockfile_and_installed_disagreement_keeps_both_claims(tmp_path: Path) -> None:
    _write_metadata(tmp_path, "fastapi", "0.115.0")
    inventory = _inventory([_dependency("fastapi", ">=0.115")])
    lockfile = _UV_LOCK.replace('version = "0.115.0"', 'version = "0.116.0"')
    result = resolve_inventory(
        inventory, root=tmp_path, scan=_scan({"uv.lock": lockfile}), settings=_settings(tmp_path)
    )
    assert len(result.conflicts) == 1
    conflict = result.conflicts[0]
    assert conflict.name == "fastapi"
    assert conflict.versions == ["0.115.0", "0.116.0"]
    assert conflict.chosen == "0.116.0"
    assert "resolution ladder" in conflict.reason
    fastapi = result.inventory.by_name("fastapi")
    assert fastapi is not None
    assert fastapi.version.method is ResolutionMethod.LOCKFILE


def test_manifest_pin_and_lockfile_disagreement(tmp_path: Path) -> None:
    inventory = _inventory([_dependency("fastapi", "==0.115.0")])
    lockfile = _UV_LOCK.replace('version = "0.115.0"', 'version = "0.116.0"')
    result = resolve_inventory(
        inventory, root=tmp_path, scan=_scan({"uv.lock": lockfile}), settings=_settings(tmp_path)
    )
    assert len(result.conflicts) == 1
    assert result.conflicts[0].versions == ["0.115.0", "0.116.0"]
    fastapi = result.inventory.by_name("fastapi")
    assert fastapi is not None
    assert fastapi.version.resolved == "0.116.0"
    assert fastapi.version.method is ResolutionMethod.LOCKFILE


def test_agreeing_sources_do_not_conflict(tmp_path: Path) -> None:
    _write_metadata(tmp_path, "fastapi", "0.115.0")
    inventory = _inventory([_dependency("fastapi", "==0.115.0")])
    result = resolve_inventory(
        inventory, root=tmp_path, scan=_scan({"uv.lock": _UV_LOCK}), settings=_settings(tmp_path)
    )
    assert result.conflicts == []
    fastapi = result.inventory.by_name("fastapi")
    assert fastapi is not None
    assert fastapi.version.is_resolved


# ---------------------------------------------------------------- unresolved


def test_unresolved_range_records_reason_and_no_target(tmp_path: Path) -> None:
    inventory = _inventory([_dependency("fastapi", ">=0.115")])
    result = resolve_inventory(
        inventory, root=tmp_path, scan=_scan({}), settings=_settings(tmp_path)
    )
    fastapi = result.inventory.by_name("fastapi")
    assert fastapi is not None
    assert not fastapi.version.is_resolved
    assert any("stays unresolved" in note for note in fastapi.version.notes)
    assert result.stats["unresolved"] == 1
    assert result.inventory.knowledge_targets == []


def test_no_scan_leaves_entries_untouched(tmp_path: Path) -> None:
    inventory = _inventory([_dependency("fastapi", ">=0.115")])
    result = resolve_inventory(inventory, root=tmp_path, scan=None, settings=_settings(tmp_path))
    fastapi = result.inventory.by_name("fastapi")
    assert fastapi is not None
    assert fastapi.version.resolved is None
    assert fastapi.version.notes == []


def test_scanner_warnings_about_lockfiles_are_propagated(tmp_path: Path) -> None:
    inventory = _inventory([_dependency("fastapi", ">=0.115")])
    scan = _scan({})
    scan.warnings.append("Lockfile uv.lock exceeds the configured size cap and was skipped")
    result = resolve_inventory(inventory, root=tmp_path, scan=scan, settings=_settings(tmp_path))
    assert any("size cap" in warning for warning in result.warnings)


# ---------------------------------------------------------------- acceptance


def _locked_version(dependency: Dependency, fallback: str) -> str:
    version, method = detect_pin(dependency.version_spec, dependency.ecosystem)
    if method is ResolutionMethod.MANIFEST_PIN and version:
        return version
    return fallback


@pytest.mark.parametrize(
    ("fixture", "lockfile"), [("fastapi-app", "uv.lock"), ("nextjs-app", "package-lock.json")]
)
def test_fixture_corpus_resolves_direct_dependencies(
    fixture: str, lockfile: str, tmp_path: Path, copy_fixture
) -> None:
    repo = copy_fixture(fixture)
    profile = analyze_repository(repo, load_settings(repo, env={})).profile
    if lockfile == "uv.lock":
        lines = ["version = 1", ""]
        for dependency in profile.dependencies:
            if dependency.ecosystem != "pypi":
                continue
            lines += [
                "[[package]]",
                f'name = "{dependency.name}"',
                f'version = "{_locked_version(dependency, "1.0.0")}"',
                "",
            ]
        (repo / lockfile).write_text("\n".join(lines), encoding="utf-8")
    else:
        packages: dict[str, object] = {"": {"name": "demo", "version": "1.0.0"}}
        for dependency in profile.dependencies:
            if dependency.ecosystem != "npm":
                continue
            packages[f"node_modules/{dependency.name}"] = {
                "version": _locked_version(dependency, "9.9.9")
            }
        (repo / lockfile).write_text(
            json.dumps({"name": "demo", "lockfileVersion": 3, "packages": packages}),
            encoding="utf-8",
        )
    settings = load_settings(repo, env={}, overrides={"knowledge": {"read_lockfiles": True}})
    result = resolve_repository(repo, settings)
    stats = result.stats
    assert stats["direct_packages"] > 0
    assert stats["resolved"] / stats["direct_packages"] >= 0.9
    assert stats["resolved_lockfile"] >= 1
    for entry in result.inventory.entries:
        if entry.version.is_resolved:
            assert entry.version.method in (
                ResolutionMethod.LOCKFILE,
                ResolutionMethod.INSTALLED_ARTIFACT,
                ResolutionMethod.MANIFEST_PIN,
            )
        elif entry.relationship is DependencyRelationship.DIRECT and entry.ecosystem in (
            "pypi",
            "npm",
        ):
            assert entry.version.notes, entry.name


def test_read_lockfiles_disabled_keeps_manifest_only_behaviour(
    tmp_path: Path, copy_fixture
) -> None:
    repo = copy_fixture("nextjs-app")
    (repo / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "demo", "version": "1.0.0"},
                    "node_modules/next": {"version": "15.0.0"},
                },
            }
        ),
        encoding="utf-8",
    )
    settings = load_settings(repo, env={})
    result = resolve_repository(repo, settings)
    assert result.stats["resolved_lockfile"] == 0
    next_dep = result.inventory.by_name("next")
    assert next_dep is not None
    assert next_dep.version.method is ResolutionMethod.MANIFEST_PIN
    assert next_dep.version.resolved == "16.0.1"


def test_scan_without_read_lockfiles_has_no_content(tmp_path: Path) -> None:
    (tmp_path / "uv.lock").write_text(_UV_LOCK, encoding="utf-8")
    result = scan_repository(tmp_path)
    assert result.read("uv.lock") is None
