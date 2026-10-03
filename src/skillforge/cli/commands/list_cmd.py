"""``skillforge list`` — planned and generated skills."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.table import Table

from skillforge.analyzer import analyze_repository
from skillforge.cli.common import REPO_ARGUMENT, emit_json, get_state, handle_errors
from skillforge.errors import NotFoundError
from skillforge.planner import SkillPlanner
from skillforge.skills import read_bundle
from skillforge.utils.fs import human_bytes


def register(app: typer.Typer) -> None:
    @app.command("list")
    @handle_errors
    def list_skills(
        ctx: typer.Context,
        path: Path = REPO_ARGUMENT,
        generated: bool = typer.Option(
            False, "--generated", help="List skills already written to the output directory."
        ),
        planned: bool = typer.Option(
            False, "--planned", help="List recommended (not yet generated) skills."
        ),
        json_output: bool = typer.Option(False, "--json", help="Print JSON."),
    ) -> None:
        """List recommended and/or generated skills."""
        state = get_state(ctx)
        state.json_mode = state.json_mode or json_output
        repo_root = path.expanduser().resolve()
        if not repo_root.is_dir():
            raise NotFoundError(
                f"Not a directory: {repo_root}",
                hint="pass the path of the repository you want to work on",
            )
        settings = state.settings_for(repo_root)
        show_both = not planned and not generated

        entries: list[dict] = []
        if planned or show_both:
            profile = analyze_repository(repo_root, settings).profile
            plan = SkillPlanner().plan(profile)
            for candidate in plan.candidates:
                entries.append(
                    {
                        "name": candidate.name,
                        "state": "recommended",
                        "reason": candidate.reason,
                        "confidence": candidate.confidence,
                        "dependencies": candidate.dependencies,
                        "path": "",
                    }
                )
        if generated or show_both:
            output_dir = settings.output_dir(repo_root)
            if output_dir.is_dir():
                for skill_dir in sorted(child for child in output_dir.iterdir() if child.is_dir()):
                    if not (skill_dir / "SKILL.md").is_file():
                        continue
                    try:
                        stored = read_bundle(skill_dir)
                    except Exception as exc:
                        entries.append(
                            {
                                "name": skill_dir.name,
                                "state": "invalid",
                                "reason": str(exc),
                                "confidence": 0.0,
                                "dependencies": [],
                                "path": str(skill_dir),
                            }
                        )
                        continue
                    size = sum(
                        len(content.encode("utf-8"))
                        for content in stored.bundle.all_contents().values()
                    )
                    entries.append(
                        {
                            "name": stored.name,
                            "state": "generated",
                            "reason": stored.manifest.reason if stored.manifest else "",
                            "confidence": stored.manifest.confidence if stored.manifest else 0.0,
                            "dependencies": [],
                            "path": str(skill_dir),
                            "bytes": size,
                            "files": len(stored.bundle.files) + 1,
                        }
                    )

        if state.json_mode:
            emit_json(state, {"repository": repo_root.name, "skills": entries})
            return

        console = state.console
        table = Table(title=f"Skills for {repo_root.name}", title_justify="left")
        table.add_column("Skill", style="bold cyan")
        table.add_column("State")
        table.add_column("Conf.", justify="right")
        table.add_column("Size", justify="right")
        table.add_column("Reason", style="dim")
        for entry in entries:
            table.add_row(
                entry["name"],
                entry["state"],
                f"{entry.get('confidence', 0.0):.2f}",
                human_bytes(int(entry.get("bytes", 0))) if entry.get("bytes") else "—",
                (entry.get("reason") or "")[:80],
            )
        console.print(table)
        if not entries:
            console.print("[dim]Nothing to show. Run [bold]skillforge analyze[/bold] first.[/dim]")


__all__ = ["register"]
