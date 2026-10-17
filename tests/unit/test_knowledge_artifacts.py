"""Installed-artifact locator tests (K2)."""

from __future__ import annotations

import json
from pathlib import Path

from skillforge.knowledge.artifacts import collect_installed


def _write_metadata(path: Path, name: str, version: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n\nBody\n",
        encoding="utf-8",
    )


def _write_package_json(path: Path, name: str, version: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"name": name, "version": version}), encoding="utf-8")


def _posix_dist_info(root: Path, name: str, version: str) -> Path:
    return (
        root
        / ".venv"
        / "lib"
        / "python3.12"
        / "site-packages"
        / f"{name}-{version}.dist-info"
        / "METADATA"
    )


def test_python_posix_layout(tmp_path: Path) -> None:
    _write_metadata(_posix_dist_info(tmp_path, "fastapi", "0.115.0"), "fastapi", "0.115.0")
    index = collect_installed(tmp_path)
    artifact = index.artifacts[("pypi", "fastapi")]
    assert artifact.version == "0.115.0"
    assert artifact.path.startswith(".venv/")


def test_python_windows_layout(tmp_path: Path) -> None:
    metadata = tmp_path / ".venv" / "Lib" / "site-packages" / "httpx-0.27.2.dist-info" / "METADATA"
    _write_metadata(metadata, "httpx", "0.27.2")
    index = collect_installed(tmp_path)
    assert index.artifacts[("pypi", "httpx")].version == "0.27.2"


def test_node_modules_direct_and_scoped(tmp_path: Path) -> None:
    _write_package_json(tmp_path / "node_modules" / "next" / "package.json", "next", "16.0.1")
    _write_package_json(
        tmp_path / "node_modules" / "@prisma" / "client" / "package.json",
        "@prisma/client",
        "6.1.0",
    )
    (tmp_path / "node_modules" / ".bin").mkdir(parents=True)
    index = collect_installed(tmp_path)
    assert index.artifacts[("npm", "next")].version == "16.0.1"
    assert index.artifacts[("npm", "@prisma/client")].version == "6.1.0"


def test_component_node_modules(tmp_path: Path) -> None:
    _write_package_json(
        tmp_path / "web" / "node_modules" / "react" / "package.json", "react", "19.0.0"
    )
    assert ("npm", "react") not in collect_installed(tmp_path).artifacts
    index = collect_installed(tmp_path, component_paths=("web",))
    assert index.artifacts[("npm", "react")].version == "19.0.0"


def test_broken_package_json_is_skipped(tmp_path: Path) -> None:
    package_json = tmp_path / "node_modules" / "broken" / "package.json"
    package_json.parent.mkdir(parents=True)
    package_json.write_text("{not json", encoding="utf-8")
    index = collect_installed(tmp_path)
    assert ("npm", "broken") not in index.artifacts


def test_artifact_cap_records_warning(tmp_path: Path) -> None:
    for name in ("fastapi", "httpx", "anyio"):
        _write_metadata(_posix_dist_info(tmp_path, name, "1.0.0"), name, "1.0.0")
    index = collect_installed(tmp_path, max_artifacts=1)
    assert len(index.artifacts) == 1
    assert any("capped" in warning for warning in index.warnings)


def test_metadata_without_version_is_ignored(tmp_path: Path) -> None:
    metadata = _posix_dist_info(tmp_path, "mystery", "1.0.0")
    metadata.parent.mkdir(parents=True, exist_ok=True)
    metadata.write_text("Metadata-Version: 2.1\nName: mystery\n\nBody\n", encoding="utf-8")
    index = collect_installed(tmp_path)
    assert ("pypi", "mystery") not in index.artifacts


def test_python_metadata_declared_urls(tmp_path: Path) -> None:
    metadata = _posix_dist_info(tmp_path, "demo-pkg", "1.2.3")
    metadata.parent.mkdir(parents=True, exist_ok=True)
    metadata.write_text(
        "Metadata-Version: 2.1\n"
        "Name: demo-pkg\n"
        "Version: 1.2.3\n"
        "Home-page: https://demo.example/\n"
        "Project-URL: Documentation, https://docs.demo.example/1.2.3/\n"
        "Project-URL: Source, git+https://github.com/example/demo-pkg.git\n"
        "Project-URL: Changelog, https://github.com/example/demo-pkg/releases\n"
        "\nBody\n",
        encoding="utf-8",
    )
    artifact = collect_installed(tmp_path).artifacts[("pypi", "demo-pkg")]
    assert artifact.urls["homepage"] == ["https://demo.example/"]
    assert artifact.urls["documentation"] == ["https://docs.demo.example/1.2.3/"]
    assert artifact.urls["repository"] == ["https://github.com/example/demo-pkg"]
    assert artifact.urls["changelog"] == ["https://github.com/example/demo-pkg/releases"]


def test_node_package_json_declared_urls(tmp_path: Path) -> None:
    package_json = tmp_path / "node_modules" / "demo" / "package.json"
    package_json.parent.mkdir(parents=True)
    package_json.write_text(
        json.dumps(
            {
                "name": "demo",
                "version": "3.0.0",
                "homepage": "https://demo.dev/",
                "repository": {"type": "git", "url": "git+https://github.com/example/demo.git"},
                "bugs": {"url": "https://github.com/example/demo/issues"},
                "documentation": "https://demo.dev/docs/",
            }
        ),
        encoding="utf-8",
    )
    artifact = collect_installed(tmp_path).artifacts[("npm", "demo")]
    assert artifact.urls["homepage"] == ["https://demo.dev/"]
    assert artifact.urls["repository"] == ["https://github.com/example/demo"]
    assert artifact.urls["issues"] == ["https://github.com/example/demo/issues"]
    assert artifact.urls["documentation"] == ["https://demo.dev/docs/"]
