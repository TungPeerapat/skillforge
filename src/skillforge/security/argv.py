"""Command-string safety helpers for controlled execution.

SkillForge executes repository commands as argument vectors, never through a
shell. This module is the single place that decides whether a discovered
command string can be safely represented as an argv list, and it rejects
anything that would change meaning without a shell: pipelines, redirections,
command substitution, backgrounding, globs, and variable expansion.
"""

from __future__ import annotations

import shlex
from typing import Final

#: Two-character shell operators that must never survive into an argv list.
SHELL_OPERATORS: Final[tuple[str, ...]] = ("&&", "||", "$(", "${", ">>", "<<")

#: Single characters with shell meaning outside quotes.
SHELL_CHARACTERS: Final[str] = "|;&<>`\n\r^"

#: Characters that rely on shell globbing/expansion to be useful.
_GLOB_CHARACTERS: Final[str] = "*?"


def contains_shell_syntax(command: str) -> bool:
    """True when ``command`` needs a shell and must not be run as argv.

    Quoted sections are ignored, so ``grep -r 'a|b' .`` is representable while
    ``a | b`` is not.
    """
    if not command or not command.strip():
        return False
    quote: str | None = None
    index = 0
    length = len(command)
    while index < length:
        character = command[index]
        if quote is not None:
            if character == quote:
                quote = None
            index += 1
            continue
        if character in ("'", '"'):
            quote = character
            index += 1
            continue
        if command.startswith(SHELL_OPERATORS, index):
            return True
        if character in SHELL_CHARACTERS:
            return True
        if character in ("$", "%"):
            # Variable expansion is not performed for argv execution; refusing
            # is safer than running a literal ``$VAR`` by mistake.
            return True
        if character in _GLOB_CHARACTERS:
            return True
        if character == "\\" and index + 1 < length:
            # Escape outside quotes changes meaning without a shell.
            return True
        index += 1
    return False


def split_argv(command: str) -> list[str] | None:
    """Parse ``command`` into an argument vector, or ``None`` if unsafe.

    The parser is POSIX-style; repository commands that only make sense in
    cmd.exe or PowerShell (for example more than one quoted Windows path) are
    rejected rather than guessed.
    """
    stripped = command.strip()
    if not stripped or contains_shell_syntax(stripped):
        return None
    try:
        argv = shlex.split(stripped, posix=True)
    except ValueError:
        return None
    if not argv:
        return None
    return argv


__all__ = ["SHELL_CHARACTERS", "SHELL_OPERATORS", "contains_shell_syntax", "split_argv"]
