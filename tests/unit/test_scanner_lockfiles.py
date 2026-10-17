"""Scanner lockfile opt-in tests (K2)."""

from __future__ import annotations

from pathlib import Path

from skillforge.analyzer.languages import FileCategory
from skillforge.analyzer.scanner import ScanOptions, SkipReason, scan_repository


def test_lockfiles_are_not_retained_by_default(tmp_path: Path) -> None:
    (tmp_path / "package-lock.json").write_text(
        '{"lockfileVersion": 3, "packages": {}}', encoding="utf-8"
    )
    result = scan_repository(tmp_path)
    assert result.read("package-lock.json") is None
    skipped = [item for item in result.skipped if item.path == "package-lock.json"]
    assert skipped and skipped[0].reason is SkipReason.NO_CONTENT_POLICY


def test_read_lockfiles_retains_bounded_content(tmp_path: Path) -> None:
    text = '{"lockfileVersion": 3, "packages": {"node_modules/next": {"version": "16.0.1"}}}'
    (tmp_path / "package-lock.json").write_text(text, encoding="utf-8")
    result = scan_repository(tmp_path, ScanOptions(read_lockfiles=True))
    assert result.read("package-lock.json") == text
    record = result.record("package-lock.json")
    assert record is not None
    assert record.category is FileCategory.LOCKFILE


def test_oversized_lockfile_is_skipped_with_a_warning(tmp_path: Path) -> None:
    (tmp_path / "uv.lock").write_text("x" * 500, encoding="utf-8")
    result = scan_repository(tmp_path, ScanOptions(read_lockfiles=True, max_lockfile_size=64))
    assert result.read("uv.lock") is None
    assert any("size cap" in warning for warning in result.warnings)
    skipped = [item for item in result.skipped if item.path == "uv.lock"]
    assert skipped and skipped[0].reason is SkipReason.TOO_LARGE


def test_lockfile_options_default_off() -> None:
    options = ScanOptions()
    assert options.read_lockfiles is False
    assert options.max_lockfile_size == 32 * 1024 * 1024
