"""Tests for the scanner and ignore rules."""

from __future__ import annotations

from pathlib import Path

from tests.conftest import write_file

from skillforge.analyzer.ignore import (
    IgnoreMatcher,
    is_secret_file,
    parse_gitignore,
)
from skillforge.analyzer.languages import FileCategory
from skillforge.analyzer.scanner import ScanOptions, SkipReason, scan_repository


def test_default_ignored_directories_are_not_traversed(tmp_path: Path) -> None:
    write_file(tmp_path, "app.py", "print('ok')\n")
    write_file(tmp_path, "node_modules/left-pad/index.js", "module.exports = 1\n")
    write_file(tmp_path, ".git/config", "[core]\n")
    write_file(tmp_path, "__pycache__/app.cpython-312.pyc", "x")
    write_file(tmp_path, "build/output.txt", "x")

    result = scan_repository(tmp_path, ScanOptions())
    paths = {record.path for record in result.files}
    assert paths == {"app.py"}
    assert result.ignored_entries > 0


def test_gitignore_patterns_are_honoured(tmp_path: Path) -> None:
    write_file(
        tmp_path,
        ".gitignore",
        "# comment\n*.log\n/build/\n!important.log\n**/tmp\nnested/only-here.txt\n",
    )
    write_file(tmp_path, "app.log", "noise")
    write_file(tmp_path, "important.log", "keep")
    write_file(tmp_path, "build/index.html", "<html></html>")
    write_file(tmp_path, "src/tmp/cache.txt", "x")
    write_file(tmp_path, "nested/only-here.txt", "x")
    write_file(tmp_path, "nested/other.txt", "keep")
    write_file(tmp_path, "src/config.yaml", "a: 1\n")

    result = scan_repository(tmp_path, ScanOptions())
    paths = {record.path for record in result.files}
    assert "app.log" not in paths
    assert "important.log" in paths  # negated
    assert "build/index.html" not in paths
    assert "src/tmp/cache.txt" not in paths
    assert "nested/only-here.txt" not in paths
    assert "nested/other.txt" in paths
    assert "src/config.yaml" in paths


def test_nested_gitignore_applies_to_its_directory(tmp_path: Path) -> None:
    write_file(tmp_path, "root.py", "x = 1\n")
    write_file(tmp_path, "sub/.gitignore", "generated.py\n")
    write_file(tmp_path, "sub/generated.py", "x = 1\n")
    write_file(tmp_path, "sub/kept.py", "x = 1\n")

    result = scan_repository(tmp_path, ScanOptions())
    paths = {record.path for record in result.files}
    assert paths == {"root.py", "sub/kept.py"}


def test_negation_last_match_wins(tmp_path: Path) -> None:
    write_file(tmp_path, ".gitignore", "*.env\n!keep.env\n")
    write_file(tmp_path, "a.env", "x")
    write_file(tmp_path, "keep.env", "x")
    matcher = IgnoreMatcher(parse_gitignore("*.env\n!keep.env\n", source=".gitignore"))
    assert matcher.is_ignored("a.env", is_dir=False)
    assert not matcher.is_ignored("keep.env", is_dir=False)
    # A later pattern overrides the negation again.
    re_ignored = IgnoreMatcher(parse_gitignore("*.env\n!keep.env\nkeep.env\n", source=".gitignore"))
    assert re_ignored.is_ignored("keep.env", is_dir=False)


def test_directory_only_pattern_does_not_match_files() -> None:
    matcher = IgnoreMatcher(parse_gitignore("build/\n", source=".gitignore"))
    assert matcher.is_ignored("build", is_dir=True)
    assert matcher.is_ignored("build/main.js", is_dir=False)
    assert not matcher.is_ignored("build", is_dir=False)


def test_ignore_patterns_match_nested_content() -> None:
    matcher = IgnoreMatcher(parse_gitignore("docs/build/\n", source=".gitignore"))
    assert matcher.is_ignored("docs/build", is_dir=True)
    assert matcher.is_ignored("docs/build/gen.txt", is_dir=False)
    assert not matcher.is_ignored("other/build/gen.txt", is_dir=False)


def test_secret_files_are_never_read(tmp_path: Path) -> None:
    write_file(tmp_path, ".env", "API_KEY=sk-proj-realsecretvalue1234567890\n")
    write_file(tmp_path, ".env.example", "API_KEY=your-key-here\n")
    write_file(tmp_path, "certs/server.pem", "-----BEGIN PRIVATE KEY-----\n")
    write_file(tmp_path, "app.py", "x = 1\n")

    result = scan_repository(tmp_path, ScanOptions())
    assert result.read(".env") is None
    assert result.read(".env.example") == "API_KEY=your-key-here\n"
    assert result.read("certs/server.pem") is None
    assert result.read("app.py") is not None
    secret_skips = [item for item in result.skipped if item.reason is SkipReason.SECRET]
    assert {item.path for item in secret_skips} == {".env", "certs/server.pem"}


def test_secret_file_classification() -> None:
    assert is_secret_file(".env")
    assert is_secret_file(".env.production")
    assert is_secret_file("id_rsa")
    assert is_secret_file("service.key")
    assert is_secret_file("app.sqlite3")
    assert not is_secret_file(".env.example")
    assert not is_secret_file("server.pem.example")
    assert not is_secret_file("main.py")


def test_binary_files_are_skipped(tmp_path: Path) -> None:
    (tmp_path / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
    write_file(tmp_path, "app.py", "x = 1\n")
    result = scan_repository(tmp_path, ScanOptions())
    assert "image.png" not in {record.path for record in result.files}
    assert result.skip_count(SkipReason.BINARY) == 1


def test_large_files_are_not_read(tmp_path: Path) -> None:
    write_file(tmp_path, "big.py", "x = 1\n" * 100)
    result = scan_repository(tmp_path, ScanOptions(max_file_size=50))
    assert result.read("big.py") is None
    assert result.skip_count(SkipReason.TOO_LARGE) == 1
    assert result.record("big.py") is not None  # presence still recorded


def test_symlinks_are_not_followed_by_default(tmp_path: Path) -> None:
    write_file(tmp_path, "real.py", "x = 1\n")
    link = tmp_path / "link.py"
    try:
        link.symlink_to(tmp_path / "real.py")
    except (OSError, NotImplementedError):
        return
    result = scan_repository(tmp_path, ScanOptions())
    assert result.skip_count(SkipReason.SYMLINK) == 1
    assert result.read("link.py") is None


def test_lockfiles_are_recorded_without_content(tmp_path: Path) -> None:
    write_file(tmp_path, "package.json", "{}")
    write_file(tmp_path, "package-lock.json", '{"lockfileVersion": 3}')
    result = scan_repository(tmp_path, ScanOptions())
    lock = result.record("package-lock.json")
    assert lock is not None
    assert lock.category is FileCategory.LOCKFILE
    assert result.read("package-lock.json") is None


def test_include_filters_files(tmp_path: Path) -> None:
    write_file(tmp_path, "keep.py", "x = 1\n")
    write_file(tmp_path, "skip.py", "x = 1\n")
    result = scan_repository(tmp_path, ScanOptions(include=("keep.py",)))
    assert {record.path for record in result.files} == {"keep.py"}


def test_custom_ignore_patterns(tmp_path: Path) -> None:
    write_file(tmp_path, "generated/api.py", "x = 1\n")
    write_file(tmp_path, "src/api.py", "x = 1\n")
    result = scan_repository(tmp_path, ScanOptions(extra_ignore=("generated/",)))
    assert {record.path for record in result.files} == {"src/api.py"}


def test_generated_files_are_detected(tmp_path: Path) -> None:
    write_file(tmp_path, "schema.py", "# Code generated by protoc. DO NOT EDIT.\n")
    write_file(tmp_path, "app.js", "console.log('hi')\n")
    write_file(tmp_path, "bundle.min.js", "var a=1;")
    result = scan_repository(tmp_path, ScanOptions())
    assert result.record("schema.py") is not None
    assert result.record("schema.py").is_generated is True
    assert result.record("bundle.min.js").is_generated is True
    assert result.record("app.js").is_generated is False


def test_scanner_records_directories_for_platform_detection(tmp_path: Path) -> None:
    write_file(tmp_path, "android/app/build.gradle", "// android\n")
    write_file(tmp_path, "lib/main.dart", "void main() {}\n")
    result = scan_repository(tmp_path, ScanOptions())
    assert result.has_directory("android")
    assert result.has_directory("lib")
    assert not result.has_directory("ios")
