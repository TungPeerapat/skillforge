"""Version parser tests (K2): normalization, comparison, agreement."""

from __future__ import annotations

import pytest

from skillforge.knowledge.versions import (
    normalize_version,
    parser_for,
    versions_agree,
)


def test_pep440_normalization_uses_packaging() -> None:
    assert normalize_version("pypi", "1.0") == "1.0"
    assert normalize_version("pypi", "01.2") == "1.2"
    assert normalize_version("pypi", "2.0.0.post1") == "2.0.0.post1"
    assert normalize_version("pypi", "not-a-version") is None


def test_pep440_agreement() -> None:
    # PEP 440 treats 1.0 and 1.0.0 as the same version; that is a feature.
    assert versions_agree("pypi", "1.0", "1.0.0")
    assert not versions_agree("pypi", "1.0", "1.1")


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("1.2.3", "1.2.3", 0),
        ("1.2.3", "1.2.4", -1),
        ("2.0.0", "1.9.9", 1),
        ("1.2.3-alpha", "1.2.3", -1),
        ("1.2.3-alpha.2", "1.2.3-alpha.10", -1),
    ],
)
def test_semver_comparison(left: str, right: str, expected: int) -> None:
    parser = parser_for("npm")
    assert parser.compare(left, right) == expected


def test_semver_agreement_strips_v_prefix() -> None:
    assert versions_agree("npm", "1.2.3", "v1.2.3")
    assert versions_agree("go", "v1.10.0", "v1.10.0")
    assert not versions_agree("go", "v1.10.0", "v1.9.0")


def test_nuget_comparison() -> None:
    parser = parser_for("nuget")
    assert parser.normalize("1.2.3-Beta") == "1.2.3-beta"
    assert parser.compare("1.2.3-beta", "1.2.3") == -1
    assert parser.compare("13.0.3", "13.0.4") == -1


def test_maven_comparison_is_numeric_per_part() -> None:
    parser = parser_for("maven")
    assert parser.compare("1.10.0", "1.9.0") == 1
    assert parser.compare("2.0.20", "2.0.20") == 0


def test_unknown_ecosystem_is_exact_identity_only() -> None:
    parser = parser_for("cargo")
    assert parser.normalize("1.2.3") == "1.2.3"
    assert parser.compare("1.2.3", "1.2") is None
    assert versions_agree("cargo", "1.2.3", "1.2.3")
    assert not versions_agree("cargo", "1.2.3", "1.2")
