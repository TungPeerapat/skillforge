"""Artifact introspection tests (K6): synthetic surfaces per ecosystem."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from skillforge.knowledge.artifacts import InstalledArtifact, collect_installed
from skillforge.knowledge.capabilities import drafts_to_capabilities, normalize_capabilities
from skillforge.knowledge.introspect import (
    ArtifactLocation,
    Budgets,
    introspect_artifact,
    locate_artifact,
)
from skillforge.models import CapabilityKind, Certainty


def _coverage(found: set[str], expected: set[str]) -> float:
    return len(found & expected) / len(expected)


# ------------------------------------------------------------------- Python


def _python_package(tmp_path: Path, *, stubs: bool) -> tuple[Path, Path]:
    root = tmp_path / "project"
    site = root / ".venv" / "lib" / "python3.12" / "site-packages"
    dist = site / "demo-1.2.3.dist-info"
    dist.mkdir(parents=True)
    (dist / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: demo\nVersion: 1.2.3\n", encoding="utf-8"
    )
    (dist / "top_level.txt").write_text("demo\n", encoding="utf-8")
    (dist / "entry_points.txt").write_text(
        "[console_scripts]\ndemo = demo.cli:main\n", encoding="utf-8"
    )
    package = site / "demo"
    package.mkdir()
    (package / "__init__.py").write_text(
        '"""Demo package."""\n'
        "from .api import Client\n"
        '__all__ = ["Client", "make_request", "DEFAULT_TIMEOUT", "DemoError"]\n'
        "DEFAULT_TIMEOUT = 30\n"
        "class DemoError(Exception):\n"
        '    """Raised on demo failures."""\n'
        "async def make_request(url: str, timeout: int = DEFAULT_TIMEOUT) -> dict:\n"
        '    """Fetch a URL."""\n'
        '    raise DemoError("no")\n',
        encoding="utf-8",
    )
    if stubs:
        (package / "api.pyi").write_text(
            "from typing import Any\n"
            "class Client:\n"
            '    """HTTP client."""\n'
            "    base_url: str\n"
            "    def __init__(self, base_url: str, *, retries: int = 3) -> None: ...\n"
            "    @property\n"
            "    def healthy(self) -> bool: ...\n"
            "    async def get(self, path: str, **params: Any) -> dict: ...\n"
            "def parse(value: str) -> int: ...\n",
            encoding="utf-8",
        )
    return root, site


def test_python_prefers_stubs_and_finds_signatures(tmp_path: Path) -> None:
    root, _ = _python_package(tmp_path, stubs=True)
    artifact = collect_installed(root).artifacts[("pypi", "demo")]
    location = locate_artifact(root, artifact)
    assert location is not None
    result = introspect_artifact(location)
    found = {draft.name for draft in result.drafts}
    expected = {"Client", "get", "healthy", "parse", "demo"}
    assert _coverage(found, expected) >= 0.9

    client = next(draft for draft in result.drafts if draft.name == "Client")
    assert client.kind is CapabilityKind.CLASS
    init = next(draft for draft in result.drafts if draft.name == "__init__")
    assert init.parent_qualified == "demo.api.Client"
    assert init.signature is not None
    params = {param.name: param for param in init.signature.parameters}
    assert params["base_url"].required is True
    assert params["retries"].default == "3" and params["retries"].required is False
    assert params["retries"].kind == "keyword_only"
    getter = next(draft for draft in result.drafts if draft.name == "get")
    assert getter.signature is not None and getter.signature.async_ is True
    healthy = next(draft for draft in result.drafts if draft.name == "healthy")
    assert healthy.kind is CapabilityKind.PROPERTY

    entry = next(draft for draft in result.drafts if draft.name == "demo")
    assert entry.kind is CapabilityKind.CLI_COMMAND
    for draft in result.drafts:
        for item in draft.evidence:
            assert not Path(item.path).is_absolute()
            assert str(tmp_path) not in item.path


def test_python_ast_without_stubs_handles_classes_exceptions_and_reexports(
    tmp_path: Path,
) -> None:
    root, _ = _python_package(tmp_path, stubs=False)
    artifact = collect_installed(root).artifacts[("pypi", "demo")]
    location = locate_artifact(root, artifact)
    assert location is not None
    result = introspect_artifact(location)
    by_name = {draft.name: draft for draft in result.drafts}
    assert by_name["DemoError"].kind is CapabilityKind.EXCEPTION
    assert by_name["make_request"].kind is CapabilityKind.FUNCTION
    assert by_name["make_request"].signature is not None
    assert by_name["make_request"].signature.async_ is True
    assert by_name["DEFAULT_TIMEOUT"].kind is CapabilityKind.CONSTANT
    assert by_name["Client"].kind is CapabilityKind.CLASS
    assert by_name["Client"].confidence is Certainty.INFERENCE
    expected = {"Client", "DemoError", "make_request", "DEFAULT_TIMEOUT"}
    assert _coverage(set(by_name), expected) >= 0.9


def test_python_symbol_budget_is_enforced(tmp_path: Path) -> None:
    root, _ = _python_package(tmp_path, stubs=False)
    artifact = collect_installed(root).artifacts[("pypi", "demo")]
    location = locate_artifact(root, artifact)
    assert location is not None
    result = introspect_artifact(location, budgets=Budgets(max_symbols_per_artifact=2))
    assert len(result.drafts) <= 2


# --------------------------------------------------------------------- Node


def _node_package(tmp_path: Path) -> ArtifactLocation:
    package = tmp_path / "node_modules" / "demo"
    package.mkdir(parents=True)
    (package / "package.json").write_text(
        json.dumps(
            {
                "name": "demo",
                "version": "3.0.0",
                "types": "index.d.ts",
                "exports": {".": {"types": "./index.d.ts", "default": "./index.js"}},
                "bin": {"demo-cli": "./bin.js"},
            }
        ),
        encoding="utf-8",
    )
    (package / "index.d.ts").write_text(
        "export declare class Client {\n"
        "  constructor(baseUrl: string);\n"
        "  get(path: string, retries?: number): Promise<Response>;\n"
        "}\n"
        "export interface Options {\n"
        "  timeout: number;\n"
        "  retries?: number;\n"
        "}\n"
        "export declare function createClient(options: Options): Client;\n"
        "export declare function over(a: string): void;\n"
        "export declare function over(a: number): void;\n"
        "export declare const VERSION: string;\n"
        "export type ClientOptions = Options;\n"
        "export enum Mode { Fast, Slow }\n"
        "export namespace utils {\n"
        "  export function helper(value: string): number;\n"
        "}\n"
        "export { Client as APIClient };\n"
        'export * from "./extra";\n',
        encoding="utf-8",
    )
    (package / "extra.d.ts").write_text(
        "export declare function extraHelper(): void;\n", encoding="utf-8"
    )
    return ArtifactLocation(ecosystem="npm", name="demo", version="3.0.0", directory=package)


def test_node_declaration_scanner_covers_public_surface(tmp_path: Path) -> None:
    location = _node_package(tmp_path)
    result = introspect_artifact(location)
    by_name = {draft.name: draft for draft in result.drafts}
    expected = {
        "Client",
        "Options",
        "createClient",
        "VERSION",
        "ClientOptions",
        "Mode",
        "utils",
        "helper",
        "APIClient",
        "extraHelper",
        "demo-cli",
    }
    assert _coverage(set(by_name), expected) >= 0.9

    assert by_name["Client"].kind is CapabilityKind.CLASS
    assert by_name["Options"].kind is CapabilityKind.INTERFACE
    assert by_name["get"].parent_qualified == "Client"
    assert by_name["timeout"].kind is CapabilityKind.PROPERTY
    assert by_name["ClientOptions"].kind is CapabilityKind.TYPE
    assert by_name["Mode"].kind is CapabilityKind.ENUM
    assert by_name["utils"].kind is CapabilityKind.NAMESPACE
    assert by_name["helper"].qualified_name == "utils.helper"
    assert by_name["APIClient"].confidence is Certainty.INFERENCE
    assert by_name["extraHelper"].kind is CapabilityKind.FUNCTION
    assert "2 overloads" in by_name["over"].kind_detail
    assert by_name["demo-cli"].kind is CapabilityKind.CLI_COMMAND

    create = by_name["createClient"]
    assert create.signature is not None
    assert create.signature.parameters[0].type == "Options"
    assert create.signature.returns == "Client"


# ------------------------------------------------------------------.NET/Go


def test_nuget_xml_documentation(tmp_path: Path) -> None:
    root = tmp_path / "newtonsoft.json" / "13.0.3"
    lib = root / "lib" / "net6.0"
    lib.mkdir(parents=True)
    (lib / "Newtonsoft.Json.xml").write_text(
        '<?xml version="1.0"?>\n<doc><members>\n'
        '<member name="T:Newtonsoft.Json.JsonSerializer">'
        "<summary>Serializes and deserializes objects.</summary></member>\n"
        '<member name="M:Newtonsoft.Json.JsonSerializer.Serialize(System.Object)">'
        "<summary>Serializes the specified object.</summary></member>\n"
        '<member name="P:Newtonsoft.Json.JsonSerializer.Formatting">'
        "<summary>Gets or sets formatting.</summary></member>\n"
        '<member name="F:Newtonsoft.Json.JsonSerializer.MaxDepth">'
        "<summary>Maximum depth.</summary></member>\n"
        '<member name="E:Newtonsoft.Json.JsonSerializer.Error">'
        "<summary>Error event.</summary></member>\n"
        "</members></doc>\n",
        encoding="utf-8",
    )
    (root / "Newtonsoft.Json.nuspec").write_text(
        '<?xml version="1.0"?>\n<package><metadata><id>Newtonsoft.Json</id>'
        "<title>Json.NET</title><description>Popular JSON framework.</description>"
        "</metadata></package>\n",
        encoding="utf-8",
    )
    location = ArtifactLocation(
        ecosystem="nuget",
        name="Newtonsoft.Json",
        version="13.0.3",
        directory=root,
    )
    result = introspect_artifact(location)
    by_name = {draft.name: draft for draft in result.drafts}
    assert by_name["JsonSerializer"].kind is CapabilityKind.CLASS
    assert by_name["Serialize"].qualified_name == "Newtonsoft.Json.JsonSerializer.Serialize"
    assert by_name["Formatting"].kind is CapabilityKind.PROPERTY
    assert by_name["MaxDepth"].kind is CapabilityKind.CONSTANT
    assert by_name["Error"].kind is CapabilityKind.EVENT
    assert by_name["Json.NET"].kind is CapabilityKind.PACKAGE


def test_go_exported_symbols(tmp_path: Path) -> None:
    root = tmp_path / "gin"
    root.mkdir()
    (root / "server.go").write_text(
        "package gin\n"
        "// Server is the engine.\n"
        "type Server struct {\n    name string\n}\n"
        "type Handler interface {\n    Handle()\n}\n"
        "func New() *Server { return &Server{} }\n"
        "func (s *Server) Start(addr string) error { return nil }\n"
        "func hidden() {}\n"
        "var DefaultTimeout = 30\n"
        'const Version = "1.0"\n'
        "const (\n    MaxConns = 100\n    minConns = 1\n)\n",
        encoding="utf-8",
    )
    location = ArtifactLocation(ecosystem="go", name="gin", version="1.10.0", directory=root)
    result = introspect_artifact(location)
    by_name = {draft.name: draft for draft in result.drafts}
    expected = {"Server", "Handler", "New", "Start", "DefaultTimeout", "Version", "MaxConns"}
    assert _coverage(set(by_name), expected) == 1.0
    assert "hidden" not in by_name and "minConns" not in by_name
    assert by_name["Server"].kind is CapabilityKind.CLASS
    assert by_name["Handler"].kind is CapabilityKind.INTERFACE
    assert by_name["Start"].kind is CapabilityKind.METHOD
    assert by_name["Start"].parent_qualified == "gin.Server"


def test_dart_public_declarations(tmp_path: Path) -> None:
    root = tmp_path / "http-1.2.2"
    lib = root / "lib"
    lib.mkdir(parents=True)
    (lib / "http.dart").write_text(
        "class Client {\n  Future<Response> get(String url) async {}\n}\n"
        "enum Mode { fast, slow }\n"
        "mixin Helpers {\n}\n"
        "String greet(String name) {\n  return name;\n}\n"
        "String _private() => '';\n",
        encoding="utf-8",
    )
    location = ArtifactLocation(ecosystem="pub", name="http", version="1.2.2", directory=root)
    result = introspect_artifact(location)
    by_name = {draft.name: draft for draft in result.drafts}
    assert {"Client", "Mode", "Helpers", "greet"} <= set(by_name)
    assert "_private" not in by_name
    assert by_name["Client"].kind is CapabilityKind.CLASS
    assert by_name["Mode"].kind is CapabilityKind.ENUM


def test_java_jar_class_listing(tmp_path: Path) -> None:
    jar = tmp_path / "guava-33.0.0.jar"
    with zipfile.ZipFile(jar, "w") as archive:
        archive.writestr("com/google/common/collect/ImmutableList.class", b"")
        archive.writestr("com/google/common/collect/ImmutableList$Builder.class", b"")
        archive.writestr("META-INF/MANIFEST.MF", b"")
    location = ArtifactLocation(
        ecosystem="maven",
        name="com.google.guava:guava",
        version="33.0.0",
        directory=jar,
    )
    result = introspect_artifact(location)
    by_name = {draft.name: draft for draft in result.drafts}
    assert by_name["ImmutableList"].kind is CapabilityKind.CLASS
    assert by_name["ImmutableList"].qualified_name == "com.google.common.collect.ImmutableList"
    assert "Builder" not in by_name


# --------------------------------------------------------- dispatcher + IR


def test_unknown_ecosystem_reports_unknown(tmp_path: Path) -> None:
    location = ArtifactLocation(
        ecosystem="cargo", name="serde", version="1.0.0", directory=tmp_path
    )
    result = introspect_artifact(location)
    assert result.drafts == []
    assert result.accuracy is Certainty.UNKNOWN
    assert result.diagnostics


def test_drafts_to_capabilities_resolves_parents_and_domains(tmp_path: Path) -> None:
    location = _node_package(tmp_path)
    result = introspect_artifact(location)
    capabilities = drafts_to_capabilities(result.drafts)
    by_name = {capability.name: capability for capability in capabilities}
    assert by_name["get"].parent_id == by_name["Client"].id
    assert "http" in by_name["get"].domains  # tokenized from qualified name
    assert by_name["get"].evidence
    assert capabilities == normalize_capabilities(capabilities)


def test_locator_refuses_metadata_traversal(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    artifact = InstalledArtifact("npm", "evil", "1.0.0", "../outside")
    assert locate_artifact(root, artifact) is None


def test_node_refuses_types_path_outside_package(tmp_path: Path) -> None:
    location = _node_package(tmp_path)
    outside = tmp_path / "outside.d.ts"
    outside.write_text("export declare class Secret {}", encoding="utf-8")
    (location.directory / "package.json").write_text(
        json.dumps({"types": "../../outside.d.ts"}), encoding="utf-8"
    )
    result = introspect_artifact(location)
    assert "Secret" not in {draft.name for draft in result.drafts}


def test_node_fallback_finds_declarations_under_node_modules(tmp_path: Path) -> None:
    location = _node_package(tmp_path)
    (location.directory / "package.json").write_text("{}", encoding="utf-8")
    assert "Client" in {draft.name for draft in introspect_artifact(location).drafts}


def test_symlink_source_is_never_read(tmp_path: Path) -> None:
    root, _ = _python_package(tmp_path, stubs=False)
    artifact = collect_installed(root).artifacts[("pypi", "demo")]
    location = locate_artifact(root, artifact)
    assert location is not None
    outside = tmp_path / "outside.py"
    outside.write_text("class Secret: pass", encoding="utf-8")
    try:
        (location.directory / "escape.py").symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation unavailable")
    assert "Secret" not in {draft.name for draft in introspect_artifact(location).drafts}


def test_empty_all_exports_nothing(tmp_path: Path) -> None:
    root, _ = _python_package(tmp_path, stubs=False)
    artifact = collect_installed(root).artifacts[("pypi", "demo")]
    location = locate_artifact(root, artifact)
    assert location is not None
    (location.directory / "__init__.py").write_text(
        "__all__ = []\ndef hidden(): pass\n", encoding="utf-8"
    )
    assert "hidden" not in {draft.name for draft in introspect_artifact(location).drafts}


def test_native_only_surface_reports_unknown(tmp_path: Path) -> None:
    location = ArtifactLocation("nuget", "native", "1.0.0", tmp_path)
    result = introspect_artifact(location)
    assert result.accuracy is Certainty.UNKNOWN
    assert result.diagnostics
