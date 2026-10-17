"""Unit tests for shell-syntax detection and argv parsing (execution safety)."""

from __future__ import annotations

import pytest

from skillforge.security.argv import contains_shell_syntax, split_argv


@pytest.mark.parametrize(
    "command",
    [
        "a && b",
        "a || b",
        "a | b",
        "a ; b",
        "a > out.txt",
        "a < in.txt",
        "echo $(date)",
        "echo `date`",
        "echo $HOME",
        "echo %PATH%",
        "rm *",
        "ls ?.py",
        "line1\nline2",
        "cmd ^ escape",
        "echo \\$literal",
    ],
)
def test_shell_syntax_is_rejected(command: str) -> None:
    assert contains_shell_syntax(command)
    assert split_argv(command) is None


@pytest.mark.parametrize(
    "command",
    [
        "pytest -q",
        "python -m app",
        "docker compose up -d postgres",
        "grep -r 'a|b' .",
        'echo "hello; world"',
        "uv run python -m app",
        "make run",
    ],
)
def test_plain_commands_are_accepted(command: str) -> None:
    assert not contains_shell_syntax(command)
    assert split_argv(command) is not None


def test_split_argv_handles_quotes() -> None:
    assert split_argv('python -m app --name "my app"') == [
        "python",
        "-m",
        "app",
        "--name",
        "my app",
    ]


def test_split_argv_rejects_empty_and_whitespace() -> None:
    assert split_argv("") is None
    assert split_argv("   ") is None


def test_split_argv_rejects_unbalanced_quotes() -> None:
    assert split_argv("python -m 'app") is None
