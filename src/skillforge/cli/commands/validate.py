"""``skillforge validate`` — check generated skills."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.panel import Panel

from skillforge.analyzer import analyze_repository
from skillforge.cli.common import REPO_ARGUMENT, emit_json, get_state, handle_errors
from skillforge.cli.ui import validation_table
from skillforge.errors import NotFoundError
from skillforge.skills import list_skill_directories
from skillforge.validator import SkillValidator


def register(app: typer.Typer) -> None:
    @app.command("validate")
    @handle_errors
    def validate(
        ctx: typer.Context,
        path: Path = REPO_ARGUMENT,
        skill: str | None = typer.Argument(None, help="Validate only this skill name."),
        json_output: bool = typer.Option(False, "--json", help="Print JSON results."),
        strict: bool = typer.Option(
            False, "--strict", help="Treat a missing .skillforge.json manifest as a warning."
        ),
        with_evidence: bool = typer.Option(
            True,
            "--evidence/--no-evidence",
            help="Check generated commands against repository evidence (runs analysis).",
        ),
    ) -> None:
        """Validate generated skills; exits 1 when any error is found."""
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

        if skill is not None:
            target = output_dir / skill
            if not target.is_dir():
                raise NotFoundError(
                    f"No generated skill named '{skill}' in {output_dir}",
                    hint="run `skillforge generate` first, or check `skillforge list --generated`",
                )
            directories = [target]
        else:
            directories = list_skill_directories(output_dir)

        if not directories:
            raise NotFoundError(
                f"No generated skills found in {output_dir}",
                hint="run `skillforge generate <repo>` first",
            )

        profile = None
        if with_evidence:
            profile = analyze_repository(repo_root, settings).profile
        validator = SkillValidator(profile=profile, strict=strict)
        results = [validator.validate_dir(directory) for directory in directories]

        if state.json_mode:
            emit_json(
                state,
                {
                    "output": str(output_dir),
                    "results": [
                        {
                            "target": result.target,
                            "ok": result.ok,
                            "summary": result.summary(),
                            "findings": [
                                finding.model_dump(mode="json")
                                for finding in result.sorted_findings()
                            ],
                            "checked": result.checked,
                        }
                        for result in results
                    ],
                },
            )
        else:
            console = state.console
            console.print()
            for result in results:
                console.print(validation_table(result))
                console.print()
                console.print(f"  {result.summary()} — {result.target}")
                console.print()

        failed = [result for result in results if not result.ok]
        if failed:
            raise typer.Exit(1)
        if not state.json_mode:
            state.console.print(
                Panel(
                    f"[green]All {len(results)} skill(s) passed validation.[/green]",
                    border_style="green",
                )
            )


__all__ = ["register"]
