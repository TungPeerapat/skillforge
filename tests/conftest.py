"""Shared pytest fixtures and helpers."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def fixture_root() -> Path:
    """Absolute path to the fixture repositories."""
    return FIXTURES


@pytest.fixture
def temp_repo(tmp_path: Path) -> Iterator[Path]:
    """An empty temporary repository root."""
    repo = tmp_path / "repo"
    repo.mkdir()
    yield repo


@pytest.fixture
def copy_fixture(tmp_path: Path):
    """Return a factory that copies a fixture repo into a temp directory."""

    def _copy(name: str) -> Path:
        source = FIXTURES / name
        if not source.is_dir():
            raise AssertionError(f"fixture not found: {source}")
        target = tmp_path / name
        shutil.copytree(source, target)
        return target

    return _copy


def write_file(root: Path, relative: str, content: str) -> Path:
    """Write a text file inside ``root``, creating parents."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path
