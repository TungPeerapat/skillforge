"""Evidence-based discovery of commands and workflows."""

from __future__ import annotations

from skillforge.discovery.commands import (
    DOC_COMMAND_RUNNERS,
    build_command,
    classify_purpose,
    commands_from_markdown,
    extract_placeholders,
    makefile_commands,
    parse_makefile,
    procfile_commands,
    script_command,
    taskfile_commands,
    unique_commands,
)
from skillforge.discovery.workflows import synthesize_workflows

__all__ = [
    "DOC_COMMAND_RUNNERS",
    "build_command",
    "classify_purpose",
    "commands_from_markdown",
    "extract_placeholders",
    "makefile_commands",
    "parse_makefile",
    "procfile_commands",
    "script_command",
    "synthesize_workflows",
    "taskfile_commands",
    "unique_commands",
]
