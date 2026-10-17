"""``skillforge list`` — planned and generated skills, with verification status."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.table import Table
from rich.text import Text

from skillforge.analyzer import analyze_repository
from skillforge.cli.common import REPO_ARGUMENT, emit_json, get_state, handle_errors
from skillforge.errors import NotFoundError
from skillforge.learning.fingerprint import check_staleness
from skillforge.models import SkillVerification, VerificationStatus
from skillforge.planner import SkillPlanner
from skillforge.skills import read_bundle
from skillforge.utils.fs import human_bytes
from skillforge.utils.git import read_git_info

_STATUS_STYLES = {
    "VERIFIED": "green",
    "UNVERIFIED": "yellow",
    "STALE": "bold red",
    "FAILED": "red",
    "LEARNING": "yellow",
}


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
                        "verification": _unverified_payload(),
                    }
                )
        git_info = None
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
                                "verification": _unverified_payload(),
                            }
                        )
                        continue
                    size = sum(
                        len(content.encode("utf-8"))
                        for content in stored.bundle.all_contents().values()
                    )
                    verification = (
                        stored.manifest.verification
                        if stored.manifest is not None
                        else SkillVerification()
                    )
                    stale = False
                    stale_reasons: list[str] = []
                    if (
                        verification.status is VerificationStatus.VERIFIED
                        and verification.fingerprint is not None
                    ):
                        if git_info is None:
                            git_info = read_git_info(repo_root)
                        report = check_staleness(
                            verification.fingerprint,
                            repo_root,
                            current_commit=git_info.commit,
                            current_dirty=git_info.dirty,
                        )
                        stale = report.stale
                        stale_reasons = report.reasons
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
                            "verification": {
                                "status": verification.status.value,
                                "display": ("STALE" if stale else verification.status.label),
                                "method": verification.method,
                                "session": verification.session_id,
                                "stale": stale,
                                "stale_reasons": stale_reasons,
                            },
                        }
                    )

        if state.json_mode:
            emit_json(state, {"repository": repo_root.name, "skills": entries})
            return

        console = state.console
        table = Table(title=f"Skills for {repo_root.name}", title_justify="left")
        table.add_column("Skill", style="bold cyan")
        table.add_column("State")
        table.add_column("Verification")
        table.add_column("Conf.", justify="right")
        table.add_column("Size", justify="right")
        table.add_column("Reason", style="dim")
        for entry in entries:
            verification = entry.get("verification", {})
            display = str(verification.get("display", "UNVERIFIED"))
            table.add_row(
                entry["name"],
                entry["state"],
                Text(display, style=_STATUS_STYLES.get(display, "")),
                f"{entry.get('confidence', 0.0):.2f}",
                human_bytes(int(entry.get("bytes", 0))) if entry.get("bytes") else "—",
                (entry.get("reason") or "")[:60],
            )
        console.print(table)
        if not entries:
            console.print("[dim]Nothing to show. Run [bold]skillforge analyze[/bold] first.[/dim]")


def _unverified_payload() -> dict:
    return {
        "status": VerificationStatus.UNVERIFIED.value,
        "display": VerificationStatus.UNVERIFIED.label,
        "method": "static-analysis",
        "session": None,
        "stale": False,
        "stale_reasons": [],
    }


__all__ = ["register"]
