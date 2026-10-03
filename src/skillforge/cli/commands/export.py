"""``skillforge export`` — write skills for a specific agent."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.table import Table

from skillforge.cli.common import REPO_ARGUMENT, emit_json, get_state, handle_errors
from skillforge.errors import NotFoundError
from skillforge.exporters import exporter_ids, get_exporter, resolve_targets
from skillforge.skills import list_skill_directories
from skillforge.utils.fs import ensure_directory


def register(app: typer.Typer) -> None:
    @app.command("export")
    @handle_errors
    def export(
        ctx: typer.Context,
        path: Path = REPO_ARGUMENT,
        to: str | None = typer.Option(
            None,
            "--to",
            help=(
                f"Target(s): {', '.join([*exporter_ids(), 'all'])}. "
                "Defaults to [export].targets from skillforge.toml, or 'all'."
            ),
            show_default=False,
        ),
        global_scope: bool = typer.Option(
            False,
            "--global",
            help="Export to the user-level skills directory instead of the repository.",
        ),
        force: bool = typer.Option(False, "--force", help="Overwrite existing exported skills."),
        dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be written."),
        json_output: bool = typer.Option(False, "--json", help="Print JSON."),
    ) -> None:
        """Export generated skills into agent-specific layouts."""
        state = get_state(ctx)
        state.json_mode = state.json_mode or json_output
        repo_root = path.expanduser().resolve()
        if not repo_root.is_dir():
            raise NotFoundError(
                f"Not a directory: {repo_root}",
                hint="pass the path of the repository you want to work on",
            )
        settings = state.settings_for(repo_root)
        output_dir = settings.output_dir(repo_root)
        directories = list_skill_directories(output_dir)
        if not directories:
            raise NotFoundError(
                f"No generated skills found in {output_dir}",
                hint="run `skillforge generate <repo>` first",
            )
        if to is None or to.strip() == "":
            configured = [item for item in settings.export.targets if item.strip()]
            targets = resolve_targets(",".join(configured)) if configured else ["portable"]
        else:
            targets = resolve_targets(to)
        if not targets:
            targets = ["portable"]

        summaries: list[dict] = []
        for target in targets:
            exporter = get_exporter(target, global_scope=global_scope)
            root = exporter.skills_root(repo_root)
            names: list[str] = []
            if not dry_run:
                ensure_directory(root)
                names = exporter.export_directory(output_dir, repo_root, force=force)
            else:
                names = [directory.name for directory in directories]
            summaries.append(
                {
                    "exporter": exporter.id,
                    "display_name": exporter.display_name,
                    "root": str(root),
                    "skills": names,
                    "notes": exporter.compatibility_notes(),
                    "dry_run": dry_run,
                }
            )

        if state.json_mode:
            emit_json(state, {"repository": repo_root.name, "targets": summaries})
            return

        console = state.console
        console.print()
        table = Table(title="Exported skills", title_justify="left")
        table.add_column("Target", style="bold cyan")
        table.add_column("Skills", justify="right")
        table.add_column("Location", style="dim")
        for summary in summaries:
            table.add_row(summary["display_name"], str(len(summary["skills"])), summary["root"])
        console.print(table)
        for summary in summaries:
            for note in summary["notes"]:
                console.print(f"[dim]{summary['display_name']}: {note}[/dim]")
        if dry_run:
            console.print("\n[bold]Dry run[/bold]: nothing was written.")
        else:
            console.print("\nSkills are ready. Restart or reload your agent to pick them up.")


__all__ = ["register"]
