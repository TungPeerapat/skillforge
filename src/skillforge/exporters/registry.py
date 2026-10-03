"""Exporter registry."""

from __future__ import annotations

from skillforge.errors import UsageError
from skillforge.exporters.adapters import (
    ClaudeCodeExporter,
    CodexExporter,
    OpenCodeExporter,
    PortableExporter,
)
from skillforge.exporters.base import BaseExporter, ExportResult

_EXPORTERS: tuple[type[BaseExporter], ...] = (
    ClaudeCodeExporter,
    CodexExporter,
    OpenCodeExporter,
    PortableExporter,
)

BUILTIN_EXPORTERS: dict[str, type[BaseExporter]] = {
    exporter.id: exporter for exporter in _EXPORTERS
}

#: Aliases accepted by ``skillforge export --to``.
EXPORTER_ALIASES: dict[str, str] = {
    "claude": "claude",
    "claude-code": "claude",
    "claudecode": "claude",
    "codex": "codex",
    "openai": "codex",
    "agents": "codex",
    "opencode": "opencode",
    "portable": "portable",
    "generic": "portable",
    "all": "all",
}


def exporter_ids() -> list[str]:
    return sorted(BUILTIN_EXPORTERS)


def resolve_targets(value: str) -> list[str]:
    """Resolve a comma-separated ``--to`` value to exporter ids."""
    requested = [item.strip().lower() for item in value.split(",") if item.strip()]
    resolved: list[str] = []
    for item in requested:
        target = EXPORTER_ALIASES.get(item)
        if target is None:
            raise UsageError(
                f"Unknown export target: '{item}'",
                hint="valid targets: " + ", ".join([*exporter_ids(), "all"]),
            )
        if target == "all":
            for known in exporter_ids():
                if known not in resolved:
                    resolved.append(known)
            continue
        if target not in resolved:
            resolved.append(target)
    return resolved


def get_exporter(name: str, *, global_scope: bool = False) -> BaseExporter:
    """Instantiate an exporter by id."""
    resolved = EXPORTER_ALIASES.get(name.strip().lower(), name.strip().lower())
    exporter_class = BUILTIN_EXPORTERS.get(resolved)
    if exporter_class is None:
        raise UsageError(
            f"Unknown exporter: '{name}'",
            hint="valid exporters: " + ", ".join(exporter_ids()),
        )
    return exporter_class(global_scope=global_scope)


__all__ = [
    "BUILTIN_EXPORTERS",
    "EXPORTER_ALIASES",
    "BaseExporter",
    "ExportResult",
    "exporter_ids",
    "get_exporter",
    "resolve_targets",
]
