"""Tests for CLI entry-point behaviour that is easy to break on Windows."""

from __future__ import annotations

from skillforge.cli.main import supports_box_characters


def test_box_characters_supported_on_utf8() -> None:
    assert supports_box_characters("utf-8")
    assert supports_box_characters("utf8")
    assert supports_box_characters(None)


def test_box_characters_unsupported_on_legacy_code_pages() -> None:
    # Thai / Western Windows code pages cannot encode Rich's box borders, so the
    # CLI must fall back to ASCII frames instead of crashing or garbling output.
    assert not supports_box_characters("cp874")
    assert not supports_box_characters("cp1252")
    assert not supports_box_characters("ascii")


def test_box_characters_unknown_encoding_is_safe() -> None:
    assert supports_box_characters("definitely-not-an-encoding") is False
