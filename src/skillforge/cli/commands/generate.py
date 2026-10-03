"""``skillforge generate`` — deterministic skill generation with optional LLM help."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from rich.table import Table

from skillforge.analyzer import AnalysisResult, ScanResult
from skillforge.cli.common import REPO_ARGUMENT, emit_json, get_state, handle_errors
from skillforge.cli.state import AppState
from skillforge.config import Settings
from skillforge.errors import NotFoundError
from skillforge.generator import SkillGenerator
from skillforge.generator.enrichment import SkillEnricher
from skillforge.logging import get_logger
from skillforge.models import GeneratedSkill, GenerationMode
from skillforge.planner import SkillPlanner
from skillforge.providers import resolve_provider
from skillforge.skills import write_generated_skill
from skillforge.utils.fs import ensure_directory
from skillforge.validator import SkillValidator

logger = get_logger("cli.generate")


def register(app: typer.Typer) -> None:
    @app.command("generate")
    @handle_errors
    def generate(
        ctx: typer.Context,
        path: Path = REPO_ARGUMENT,
        skills: list[str] = typer.Argument(
            None, help="Specific skill names to generate (default: all recommended)."
        ),
        provider: str | None = typer.Option(
            None,
            "--provider",
            help="LLM provider for optional enrichment: none | mock | openai | anthropic | openrouter.",
        ),
        allow_external_llm: bool = typer.Option(
            False,
            "--allow-external-llm",
            help="Consent to sending selected repository context to a remote provider.",
        ),
        force: bool = typer.Option(False, "--force", help="Overwrite existing generated skills."),
        dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be written."),
        only_safe: bool = typer.Option(
            False, "--only-safe", help="Restrict skills to commands classified SAFE."
        ),
        json_output: bool = typer.Option(False, "--json", help="Print a JSON summary."),
    ) -> None:
        """Generate skills for a repository (no API key required)."""
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

        analysis = _analyze(repo_root, settings)
        profile = analysis.profile
        plan = SkillPlanner().plan(profile)
        generator = SkillGenerator(settings, only_safe=only_safe)
        enricher = _resolve_enricher(
            state, settings, provider, allow_external_llm, scan=analysis.scan
        )

        if enricher is not None:
            generated = asyncio.run(
                generator.generate_async(
                    profile, names=skills or None, plan=plan, enricher=enricher
                )
            )
        else:
            generated = generator.generate(profile, names=skills or None, plan=plan)

        validator = SkillValidator(profile=profile)
        results = []
        errors: list[str] = []
        for skill in generated:
            validation = validator.validate_generated(skill, directory=output_dir / skill.name)
            results.append((skill, validation))
            if not validation.ok:
                errors.extend(f"{skill.name}: {finding.message}" for finding in validation.errors)
        if errors:
            for message in errors:
                state.print_error(message)
            raise typer.Exit(1)

        written: list[tuple[str, Path]] = []
        if not dry_run:
            ensure_directory(output_dir)
            for skill, _validation in results:
                target = write_generated_skill(
                    skill,
                    output_dir,
                    repository=profile.name,
                    git_commit=profile.git.commit,
                    git_dirty=profile.git.dirty,
                    overwrite=force,
                )
                written.append((skill.name, target))

        if state.json_mode:
            emit_json(
                state,
                {
                    "repository": profile.name,
                    "output": str(output_dir),
                    "dry_run": dry_run,
                    "skills": [
                        {
                            "name": skill.name,
                            "mode": skill.mode.value,
                            "provider": skill.provider,
                            "files": skill.bundle.paths(),
                            "warnings": skill.warnings,
                            "validation": {
                                "errors": len(validation.errors),
                                "warnings": len(validation.warnings),
                            },
                        }
                        for skill, validation in results
                    ],
                    "timings_ms": state.tracer.total_ms(),
                },
            )
            return

        console = state.console
        console.print()
        table = Table(title=f"Generated {len(results)} skill(s)", title_justify="left")
        table.add_column("Skill", style="bold cyan")
        table.add_column("Files", justify="right")
        table.add_column("Mode")
        table.add_column("Warnings", justify="right")
        table.add_column("Path", style="dim")
        for skill, validation in results:
            table.add_row(
                skill.name,
                str(len(skill.bundle.all_contents())),
                skill.mode.value,
                str(len(validation.warnings)),
                str(output_dir / skill.name),
            )
        console.print(table)
        for skill, _validation in results:
            for warning in skill.warnings:
                console.print(f"[yellow]{skill.name}: {warning}[/yellow]")
        if dry_run:
            console.print("\n[bold]Dry run[/bold]: nothing was written.")
        else:
            console.print(
                f"\n[bold]Next:[/bold] skillforge validate {path}\n"
                f"       skillforge export {path} --to all"
            )


def _analyze(repo_root: Path, settings: Settings) -> AnalysisResult:
    from skillforge.analyzer import analyze_repository

    return analyze_repository(repo_root, settings)


def _resolve_enricher(
    state: AppState,
    settings: Settings,
    provider: str | None,
    allow_external: bool,
    *,
    scan: ScanResult | None = None,
) -> SkillEnricher | None:
    requested = (provider or settings.provider.default or "none").strip().lower()
    if requested in ("", "none"):
        return None
    provider_instance = resolve_provider(
        settings, explicit=requested, allow_external=allow_external or state.allow_external_llm
    )
    if provider_instance is None:
        return None
    context_block = ""
    if scan is not None:
        from skillforge.context import select_context

        selection = select_context(
            scan,
            max_tokens=settings.analysis.max_context_tokens,
            max_files_per_category=settings.analysis.max_files_per_category,
        )
        context_block = selection.as_prompt_block()
        logger.info(
            "context selected for provider",
            extra={
                "files": len(selection.files),
                "tokens": selection.total_tokens,
                "budget": settings.analysis.max_context_tokens,
            },
        )
    if requested != "mock":
        logger.info(
            "enrichment enabled",
            extra={"provider": provider_instance.id, "model": provider_instance.model},
        )
    return SkillEnricher(provider_instance, context_block=context_block)


__all__ = ["GeneratedSkill", "GenerationMode", "register"]
