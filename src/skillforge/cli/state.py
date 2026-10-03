"""Shared CLI state."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console

from skillforge.config import Settings
from skillforge.errors import AnalysisError
from skillforge.logging import Tracer


@dataclass
class AppState:
    """State shared by all commands during one CLI invocation."""

    json_mode: bool = False
    verbose: int = 0
    debug: bool = False
    quiet: bool = False
    config_path: Path | None = None
    allow_external_llm: bool = False
    provider_override: str | None = None
    global_scope: bool = False
    console: Console = field(default_factory=Console)
    tracer: Tracer = field(default_factory=Tracer)

    #: Cache of loaded settings per repository root.
    _settings_cache: dict[Path, Settings] = field(default_factory=dict)

    def settings_for(self, repo_root: Path) -> Settings:
        """Load (and cache) validated settings for a repository root."""
        resolved = repo_root.resolve()
        if resolved not in self._settings_cache:
            from skillforge.config import load_settings

            overrides: dict[str, dict[str, object]] = {}
            if self.provider_override:
                overrides["provider"] = {"default": self.provider_override}
            if self.allow_external_llm:
                overrides["security"] = {"allow_external_transmission": True}
            self._settings_cache[resolved] = load_settings(
                resolved, config_file=self.config_path, overrides=overrides or None
            )
        return self._settings_cache[resolved]

    def print_error(self, message: str, *, hint: str | None = None) -> None:
        if self.json_mode:
            import json

            payload = {"error": {"message": message, "hint": hint}}
            self.console.print(json.dumps(payload))
        else:
            self.console.print(f"[bold red]error:[/bold red] {message}")
            if hint:
                self.console.print(f"[dim]hint: {hint}[/dim]")


def resolve_repo_root(path: Path) -> Path:
    """Validate and resolve a repository path."""
    candidate = path.expanduser()
    if not candidate.exists():
        raise AnalysisError(f"Path does not exist: {candidate}")
    if not candidate.is_dir():
        raise AnalysisError(f"Not a directory: {candidate}")
    return candidate.resolve()
