"""Skill validation orchestration."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from skillforge.logging import get_logger, trace_span
from skillforge.models import (
    FindingSeverity,
    GeneratedSkill,
    RepositoryProfile,
    SkillBundle,
    SkillManifest,
    ValidationResult,
)
from skillforge.skills.store import StoredSkill, list_skill_directories, read_bundle
from skillforge.validator.checks import DEFAULT_CHECKS, ValidationTarget

logger = get_logger("validator")

Check = Callable[[ValidationTarget, ValidationResult], None]


class SkillValidator:
    """Run the deterministic validation checks against skills or bundles."""

    def __init__(
        self,
        *,
        profile: RepositoryProfile | None = None,
        checks: Sequence[Check] | None = None,
        strict: bool = False,
    ) -> None:
        self._profile = profile
        self._checks = tuple(checks) if checks is not None else DEFAULT_CHECKS
        self._strict = strict

    # ----------------------------------------------------------------- targets
    def validate_dir(self, skill_dir: Path) -> ValidationResult:
        """Validate one skill directory; unreadable skills become findings."""
        result = ValidationResult(target=str(skill_dir))
        try:
            stored = read_bundle(skill_dir)
        except Exception as exc:
            result.add(
                "structure.unreadable",
                FindingSeverity.ERROR,
                f"could not read skill: {exc}",
                location=str(skill_dir),
            )
            return result
        return self.validate_stored(stored, result=result)

    def validate_stored(
        self, stored: StoredSkill, *, result: ValidationResult | None = None
    ) -> ValidationResult:
        active = result if result is not None else ValidationResult(target=str(stored.directory))
        if stored.metadata_error:
            active.add(
                "metadata.invalid",
                FindingSeverity.ERROR,
                f"invalid frontmatter: {stored.metadata_error}",
                location="SKILL.md",
                hint="frontmatter needs name and description as plain YAML",
            )
        self._run(
            ValidationTarget(
                bundle=stored.bundle,
                target=str(stored.directory),
                directory=stored.directory,
                manifest=stored.manifest,
                profile=self._profile,
                strict=self._strict,
                raw_skill_md=stored.raw_skill_md,
            ),
            active,
        )
        for warning in stored.warnings:
            active.add(
                "structure.read-warning", FindingSeverity.WARNING, warning, location="SKILL.md"
            )
        return active

    def validate_bundle(
        self,
        bundle: SkillBundle,
        *,
        target: str = "<generated>",
        directory: Path | None = None,
        manifest: SkillManifest | None = None,
    ) -> ValidationResult:
        result = ValidationResult(target=target)
        self._run(
            ValidationTarget(
                bundle=bundle,
                target=target,
                directory=directory,
                manifest=manifest,
                profile=self._profile,
                strict=self._strict,
            ),
            result,
        )
        return result

    def validate_generated(
        self, skill: GeneratedSkill, *, directory: Path | None = None
    ) -> ValidationResult:
        """Validate an in-memory generated skill before it is written."""
        manifest = skill.build_manifest(
            tool_version=skill.bundle.metadata.metadata.get("generator-version", ""),
            repository=skill.bundle.metadata.metadata.get("source-repository", ""),
            git_commit=None,
            git_dirty=None,
        )
        result = self.validate_bundle(
            skill.bundle, target=skill.name, directory=directory, manifest=manifest
        )
        result.notes.extend(skill.warnings)
        return result

    def validate_output_dir(self, output_dir: Path) -> list[ValidationResult]:
        """Validate every skill directory under ``output_dir``."""
        directories = list_skill_directories(output_dir)
        results: list[ValidationResult] = []
        with trace_span("validate", skills=len(directories)):
            for directory in directories:
                results.append(self.validate_dir(directory))
        return results

    # --------------------------------------------------------------- internals
    def _run(self, target: ValidationTarget, result: ValidationResult) -> None:
        for check in self._checks:
            try:
                check(target, result)
            except Exception as exc:  # pragma: no cover - a check bug must not crash the CLI
                logger.warning(
                    "validation check failed",
                    extra={
                        "check": getattr(check, "__name__", "?"),
                        "error_type": type(exc).__name__,
                    },
                )
                result.add(
                    "validator.check-failed",
                    FindingSeverity.INFO,
                    f"internal check '{getattr(check, '__name__', '?')}' failed "
                    f"({type(exc).__name__}); results may be incomplete",
                )


__all__ = ["DEFAULT_CHECKS", "SkillValidator"]
