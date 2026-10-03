"""Tests for text, markdown, token, and filesystem utilities."""

from __future__ import annotations

from pathlib import Path

import pytest

from skillforge.errors import ConfigError
from skillforge.utils.fs import (
    atomic_write_text,
    human_bytes,
    is_probably_binary,
    read_text_capped,
    sha256_text,
)
from skillforge.utils.markdown import (
    fenced_code_blocks,
    headings,
    markdown_links,
    referenced_skill_paths,
    render_frontmatter,
    split_frontmatter,
)
from skillforge.utils.text import (
    count_nonblank_lines,
    dedupe_preserving_order,
    normalize_whitespace,
    slugify,
    strip_ansi,
    strip_command_prefix,
    truncate,
)
from skillforge.utils.tokens import estimate_tokens, truncate_to_tokens
from skillforge.utils.toml import load_toml_text


def test_slugify_produces_skill_safe_names() -> None:
    assert slugify("My Project Runner!") == "my-project-runner"
    assert slugify("  leading   and trailing  ") == "leading-and-trailing"
    assert slugify("-already-slug-") == "already-slug"
    assert len(slugify("x" * 200)) <= 64


def test_text_helpers() -> None:
    assert strip_ansi("\x1b[31merror\x1b[0m") == "error"
    assert normalize_whitespace("a  b\t c\n  d ") == "a b c\nd"
    assert truncate("abcdefghij", 5) == "abcde"
    assert count_nonblank_lines("a\n\n  \nb\n") == 2
    assert dedupe_preserving_order(["a", "b", "a"]) == ["a", "b"]
    assert strip_command_prefix("$ pytest") == "pytest"
    assert strip_command_prefix("PS> dotnet test") == "dotnet test"


def test_estimate_tokens_is_monotonic() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("a" * 40) == 10
    assert estimate_tokens("a" * 400) > estimate_tokens("a" * 40)
    assert truncate_to_tokens("a" * 400, 10) == "a" * 40


def test_split_frontmatter() -> None:
    text = "---\nname: demo\ndescription: Demo skill\n---\n# Body\n"
    parsed = split_frontmatter(text)
    assert parsed.present
    assert parsed.error is None
    assert parsed.metadata == {"name": "demo", "description": "Demo skill"}
    assert parsed.body.strip() == "# Body"


def test_split_frontmatter_handles_missing_and_broken() -> None:
    assert split_frontmatter("# No frontmatter").present is False
    broken = split_frontmatter("---\nname: x\n# never closed\n")
    assert broken.present and broken.error


def test_render_frontmatter_is_deterministic() -> None:
    first = render_frontmatter({"description": "d", "name": "a", "metadata": {"b": "2", "a": "1"}})
    second = render_frontmatter({"metadata": {"a": "1", "b": "2"}, "name": "a", "description": "d"})
    assert first == second
    assert first.startswith("---\nname: a\ndescription: d")


def test_fenced_code_blocks_and_links() -> None:
    body = (
        "# Title\n\n```bash\npytest -q\nuvicorn app:app\n```\n\n"
        "See [commands](references/commands.md) and `scripts/preflight.py`.\n"
    )
    blocks = fenced_code_blocks(body)
    assert len(blocks) == 1
    assert blocks[0].lang == "bash"
    assert "pytest -q" in blocks[0].content
    assert markdown_links(body) == ["references/commands.md"]
    assert referenced_skill_paths(body) == {"references/commands.md", "scripts/preflight.py"}
    assert headings(body)[0] == (1, "Title", 1)


def test_fenced_code_blocks_inside_list() -> None:
    body = "  ~~~python\n  print('x')\n  ~~~\n"
    blocks = fenced_code_blocks(body)
    assert len(blocks) == 1
    assert "print" in blocks[0].content


def test_filesystem_helpers(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "file.txt"
    atomic_write_text(target, "hello\r\nworld")
    assert target.read_text(encoding="utf-8") == "hello\nworld"
    assert sha256_text("a") == sha256_text("a")
    assert human_bytes(1536).startswith("1.5")

    binary = tmp_path / "bin.dat"
    binary.write_bytes(b"\x00\x01\x02\x00")
    assert is_probably_binary(binary)
    text = tmp_path / "text.txt"
    text.write_text("hello world", encoding="utf-8")
    assert not is_probably_binary(text)

    big = tmp_path / "big.txt"
    big.write_text("line\n" * 100, encoding="utf-8")
    content, truncated = read_text_capped(big, 20)
    assert truncated
    assert len(content) <= 20


def test_load_toml_text_errors() -> None:
    assert load_toml_text("a = 1") == {"a": 1}
    with pytest.raises(ConfigError):
        load_toml_text("a = = 1")
