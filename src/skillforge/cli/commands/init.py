"""``skillforge init`` — create a project configuration file."""

from __future__ import annotations

from pathlib import Path

import typer

from skillforge.cli.common import REPO_ARGUMENT, emit_json, get_state, handle_errors
from skillforge.config import ENV_PREFIX
from skillforge.errors import EnvironmentError_, SkillExistsError
from skillforge.utils.fs import atomic_write_text

CONFIG_TEMPLATE = """# SkillForge configuration — see https://github.com/skillforge/skillforge
# Precedence: defaults < this file < {prefix}* environment variables < CLI flags

[project]
name = ""                        # defaults to the repository directory name

[analysis]
max_file_size = 200000           # bytes; larger files are summarised, not read
max_context_tokens = 50000       # token budget for optional LLM context
max_files_per_category = 40
follow_symlinks = false          # keep false for untrusted repositories
ignore = []                      # extra gitignore-style patterns
include = []                     # when non-empty, only matching paths are analysed

[provider]
default = "none"                 # none | mock | openai | openai-compatible | anthropic | openrouter
model = ""
base_url = ""                    # required for openai-compatible/local endpoints
api_key_env = ""                 # env var holding the key (never the key itself)
max_output_tokens = 4096
temperature = 0.0

[skills]
output = ".skills"               # where generated skills are written
author = ""
license = ""
overwrite = false

[security]
allow_command_execution = false      # not implemented in this release; keep false
allow_external_transmission = false  # consent for sending context to a remote provider
max_command_risk = "review"
redact_secrets = true

[export]
targets = []                     # for example ["claude", "codex", "opencode", "portable"]

[evaluation]
timeout_seconds = 300
"""


def register(app: typer.Typer) -> None:
    @app.command("init")
    @handle_errors
    def init(
        ctx: typer.Context,
        path: Path = REPO_ARGUMENT,
        force: bool = typer.Option(False, "--force", help="Overwrite an existing skillforge.toml."),
        gitignore: bool = typer.Option(
            True, "--gitignore/--no-gitignore", help="Add the output directory to .gitignore."
        ),
        json_output: bool = typer.Option(False, "--json", help="Print JSON."),
    ) -> None:
        """Create skillforge.toml and prepare ignore rules."""
        state = get_state(ctx)
        state.json_mode = state.json_mode or json_output
        repo_root = path.expanduser().resolve()
        if not repo_root.is_dir():
            raise EnvironmentError_(f"Not a directory: {repo_root}")
        config_path = repo_root / "skillforge.toml"
        if config_path.exists() and not force:
            raise SkillExistsError(
                f"{config_path} already exists",
                hint="pass --force to overwrite it",
            )
        atomic_write_text(config_path, CONFIG_TEMPLATE.format(prefix=ENV_PREFIX))

        gitignore_updated = False
        if gitignore:
            gitignore_path = repo_root / ".gitignore"
            entry = ".skills/"
            existing = ""
            if gitignore_path.is_file():
                existing = gitignore_path.read_text(encoding="utf-8", errors="replace")
            if entry not in existing.splitlines():
                separator = "" if existing.endswith("\n") or not existing else "\n"
                atomic_write_text(
                    gitignore_path,
                    existing + separator + f"# SkillForge generated skills\n{entry}\n",
                )
                gitignore_updated = True

        if state.json_mode:
            emit_json(
                state,
                {
                    "config": str(config_path),
                    "gitignore_updated": gitignore_updated,
                },
            )
            return
        console = state.console
        console.print(f"[green]Created[/green] {config_path}")
        if gitignore_updated:
            console.print(f"[green]Updated[/green] {repo_root / '.gitignore'}")
        console.print(
            "\n[bold]Next:[/bold]\n"
            f"  skillforge analyze {path}\n"
            f"  skillforge generate {path}\n"
            f"  skillforge validate {path}"
        )


__all__ = ["register"]
