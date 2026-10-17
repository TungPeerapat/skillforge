"""Lockfile parser corpus tests (K2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from skillforge.knowledge.lockfiles import parse_lockfile, parser_for

_CORPUS: tuple[tuple[str, set[tuple[str, str]]], ...] = (
    (
        "python/uv.lock",
        {
            ("fastapi", "0.115.0"),
            ("uvicorn", "0.30.6"),
            ("sqlalchemy", "2.0.35"),
            ("anyio", "4.4.0"),
        },
    ),
    ("python/poetry.lock", {("httpx", "0.27.2"), ("certifi", "2024.8.30")}),
    (
        "python/Pipfile.lock",
        {("requests", "2.31.0"), ("anyio", "4.4.0"), ("pytest", "8.2.2")},
    ),
    (
        "npm/package-lock.json",
        {
            ("next", "16.0.1"),
            ("react", "19.0.0"),
            ("@prisma/client", "6.1.0"),
            ("postcss", "8.4.49"),
        },
    ),
    (
        "npm/pnpm-lock.yaml",
        {("next", "16.0.1"), ("@prisma/client", "6.1.0"), ("react", "19.0.0")},
    ),
    (
        "npm/yarn.lock",
        {("@prisma/client", "6.1.0"), ("next", "16.0.1"), ("react", "19.0.0")},
    ),
    (
        "npm-berry/yarn.lock",
        {("next", "16.0.1"), ("@prisma/client", "6.1.0"), ("react", "19.0.0")},
    ),
    (
        "go/go.sum",
        {
            ("github.com/gin-gonic/gin", "v1.10.0"),
            ("github.com/stretchr/testify", "v1.9.0"),
            ("golang.org/x/text", "v0.14.0"),
        },
    ),
    (
        "dotnet/packages.lock.json",
        {("Newtonsoft.Json", "13.0.3"), ("Serilog", "4.0.0")},
    ),
    ("dart/pubspec.lock", {("http", "1.2.2"), ("flutter_lints", "5.0.0")}),
    (
        "java/gradle.lockfile",
        {
            ("com.google.guava:guava", "33.0.0-jre"),
            ("org.jetbrains.kotlin:kotlin-stdlib", "2.0.20"),
        },
    ),
)


@pytest.mark.parametrize(("relative", "expected"), _CORPUS)
def test_corpus_parses_fixture(
    relative: str, expected: set[tuple[str, str]], fixture_root: Path
) -> None:
    path = fixture_root / "lockfiles" / relative
    parsed = parse_lockfile(relative, path.read_text(encoding="utf-8"))
    assert parsed is not None
    assert parsed.warnings == ()
    found = {(entry.name, entry.version) for entry in parsed.entries}
    assert expected <= found


def test_uv_lock_skips_the_project_itself(fixture_root: Path) -> None:
    path = fixture_root / "lockfiles" / "python" / "uv.lock"
    parsed = parse_lockfile("python/uv.lock", path.read_text(encoding="utf-8"))
    assert parsed is not None
    names = {entry.name for entry in parsed.entries}
    assert "demo-app" not in names


def test_pubspec_lock_skips_path_sources(fixture_root: Path) -> None:
    path = fixture_root / "lockfiles" / "dart" / "pubspec.lock"
    parsed = parse_lockfile("dart/pubspec.lock", path.read_text(encoding="utf-8"))
    assert parsed is not None
    names = {entry.name for entry in parsed.entries}
    assert "local_utils" not in names


def test_yarn_berry_metadata_is_not_an_entry(fixture_root: Path) -> None:
    path = fixture_root / "lockfiles" / "npm-berry" / "yarn.lock"
    parsed = parse_lockfile("npm-berry/yarn.lock", path.read_text(encoding="utf-8"))
    assert parsed is not None
    assert all(entry.name != "__metadata" for entry in parsed.entries)


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("uv.lock", "[[package"),
        ("package-lock.json", "{oops"),
        ("pnpm-lock.yaml", "\t\t:"),
        ("packages.lock.json", "[unterminated"),
    ],
)
def test_malformed_lockfiles_warn_instead_of_raising(name: str, text: str) -> None:
    parsed = parse_lockfile(name, text)
    assert parsed is not None
    assert parsed.entries == ()
    assert parsed.warnings


def test_unknown_lockfile_is_not_claimed() -> None:
    assert parser_for("Cargo.lock") is None
    assert parser_for("notes.txt") is None
    assert parse_lockfile("Cargo.lock", "[[package]]") is None
