"""Generation orchestration: candidate + profile -> :class:`GeneratedSkill`.

The deterministic path is the default and requires no provider. Optional LLM
enrichment is handled by :mod:`skillforge.generator.enrichment`, which validates
and evidence-checks everything the model proposes before it is merged.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from skillforge import __version__
from skillforge.config import Settings
from skillforge.errors import ExampleError
from skillforge.generator.blueprint import SkillBlueprint, build_blueprint
from skillforge.generator.renderers import render_skill
from skillforge.logging import get_logger, trace_span
from skillforge.models import (
    DESCRIPTION_MAX,
    GeneratedSkill,
    GenerationMode,
    ProvenanceEntry,
    ProvenanceOrigin,
    RepositoryProfile,
    RiskLevel,
    SkillBundle,
    SkillCandidate,
    SkillFile,
    SkillMetadata,
    SkillPlan,
)
from skillforge.planner.planner import SkillPlanner

logger = get_logger("generator")


class SkillGenerator:
    """Turn planned candidates into portable, validated skill bundles."""

    def __init__(
        self,
        settings: Settings,
        *,
        provider: object | None = None,
        tool_version: str = __version__,
        only_safe: bool = False,
    ) -> None:
        self._settings = settings
        self._provider = provider
        self._tool_version = tool_version
        self._only_safe = only_safe
        self._max_risk = RiskLevel.from_config(settings.security.max_command_risk)

    # ------------------------------------------------------------------ single
    def generate_candidate(
        self, candidate: SkillCandidate, profile: RepositoryProfile
    ) -> GeneratedSkill:
        with trace_span("generate", skill=candidate.name):
            blueprint = build_blueprint(
                candidate, profile, only_safe=self._only_safe, max_risk=self._max_risk
            )
            body, references, scripts = render_skill(candidate.name, blueprint)
            bundle = self._assemble_bundle(candidate, blueprint, body, references, scripts)
            provenance = self._provenance(candidate, blueprint, references, scripts)
            skill = GeneratedSkill(
                bundle=bundle,
                candidate=candidate,
                provenance=provenance,
                mode=GenerationMode.DETERMINISTIC,
                provider=None,
                warnings=self._warnings(candidate, blueprint, bundle),
                created_at=datetime.now(UTC),
            )
        return skill

    # ------------------------------------------------------------------- batch
    def _select_candidates(
        self,
        profile: RepositoryProfile,
        *,
        names: Iterable[str] | None = None,
        plan: SkillPlan | None = None,
    ) -> list[SkillCandidate]:
        active_plan = plan or SkillPlanner().plan(profile)
        wanted = list(names) if names is not None else None
        if wanted:
            available = set(active_plan.names)
            unknown = [name for name in wanted if name not in available]
            if unknown:
                raise ExampleError(
                    "Skill(s) not recommended for this repository: "
                    + ", ".join(sorted(unknown))
                    + ". Run `skillforge list` to see the planned skills, or `skillforge analyze` "
                    "to inspect the evidence."
                )
            resolved: list[SkillCandidate] = []
            for name in wanted:
                found = active_plan.candidate(name)
                if found is not None:
                    resolved.append(found)
            return resolved
        return list(active_plan.candidates)

    def generate(
        self,
        profile: RepositoryProfile,
        *,
        names: Iterable[str] | None = None,
        plan: SkillPlan | None = None,
    ) -> list[GeneratedSkill]:
        """Generate all planned skills, or the named subset (deterministic path)."""
        candidates = self._select_candidates(profile, names=names, plan=plan)
        skills = [self.generate_candidate(candidate, profile) for candidate in candidates]
        self._prune_dependency_warnings(skills)
        return skills

    # ---------------------------------------------------- async (LLM-assisted)
    async def generate_candidate_async(
        self,
        candidate: SkillCandidate,
        profile: RepositoryProfile,
        *,
        enricher: object | None = None,
    ) -> GeneratedSkill:
        """Generate one skill and optionally enrich it with an LLM."""
        skill = self.generate_candidate(candidate, profile)
        if enricher is None:
            return skill
        from skillforge.generator.enrichment import SkillEnricher

        if not isinstance(enricher, SkillEnricher):  # pragma: no cover - programming error
            raise ExampleError("enricher must be a SkillEnricher instance")
        blueprint = build_blueprint(
            candidate, profile, only_safe=self._only_safe, max_risk=self._max_risk
        )
        outcome = await enricher.enrich(skill, blueprint)
        return outcome.skill

    async def generate_async(
        self,
        profile: RepositoryProfile,
        *,
        names: Iterable[str] | None = None,
        plan: SkillPlan | None = None,
        enricher: object | None = None,
    ) -> list[GeneratedSkill]:
        """Async batch generation with optional LLM enrichment."""
        candidates = self._select_candidates(profile, names=names, plan=plan)
        skills = [
            await self.generate_candidate_async(candidate, profile, enricher=enricher)
            for candidate in candidates
        ]
        self._prune_dependency_warnings(skills)
        return skills

    # --------------------------------------------------------------- internals
    def _assemble_bundle(
        self,
        candidate: SkillCandidate,
        blueprint: SkillBlueprint,
        body: str,
        references: list[SkillFile],
        scripts: list[SkillFile],
    ) -> SkillBundle:
        metadata_values = {
            "generator": "skillforge",
            "generator-version": self._tool_version,
            "source-repository": blueprint.repository,
            "certainty": candidate.certainty.value,
        }
        if self._settings.skills.author:
            metadata_values["author"] = self._settings.skills.author
        description = blueprint.description
        if len(description) > DESCRIPTION_MAX:
            description = description[: DESCRIPTION_MAX - 3].rstrip() + "..."
        files: dict[str, SkillFile] = {}
        for file in [*references, *scripts]:
            if file.path in files:
                raise ExampleError(f"duplicate generated file: {file.path}")
            files[file.path] = file
        return SkillBundle(
            metadata=SkillMetadata(
                name=candidate.name,
                description=description,
                license=self._settings.skills.license or None,
                compatibility=blueprint.compatibility,
                metadata=metadata_values,
            ),
            body=body,
            files=files,
        )

    def _provenance(
        self,
        candidate: SkillCandidate,
        blueprint: SkillBlueprint,
        references: list[SkillFile],
        scripts: list[SkillFile],
    ) -> list[ProvenanceEntry]:
        entries: list[ProvenanceEntry] = [
            ProvenanceEntry(
                target="SKILL.md",
                claim=f"skill recommended: {candidate.reason}",
                origin=ProvenanceOrigin.DETERMINISTIC,
                evidence=list(candidate.evidence),
            )
        ]
        for command in blueprint.commands:
            entries.append(
                ProvenanceEntry(
                    target="SKILL.md",
                    claim=f"documented command: {command.command}",
                    origin=ProvenanceOrigin.DETERMINISTIC,
                    evidence=list(command.evidence),
                )
            )
        for file in references:
            entries.append(
                ProvenanceEntry(
                    target=file.path,
                    claim=f"reference generated: {file.description or file.path}",
                    origin=ProvenanceOrigin.TEMPLATE,
                    evidence=[],
                )
            )
        for file in scripts:
            entries.append(
                ProvenanceEntry(
                    target=file.path,
                    claim=f"script generated: {file.description or file.path}",
                    origin=ProvenanceOrigin.TEMPLATE,
                    evidence=[],
                )
            )
        return entries

    def _warnings(
        self, candidate: SkillCandidate, blueprint: SkillBlueprint, bundle: SkillBundle
    ) -> list[str]:
        warnings: list[str] = []
        if blueprint.excluded_dangerous:
            warnings.append(
                f"{len(blueprint.excluded_dangerous)} destructive command(s) were detected and "
                "excluded from this skill: " + ", ".join(blueprint.excluded_dangerous)
            )
        if not blueprint.commands:
            warnings.append("No evidence-backed commands matched this skill.")
        if len(blueprint.unknowns) >= 3 and len(blueprint.unknowns) >= len(blueprint.commands):
            warnings.append(
                "Several open questions were recorded; review references/evidence.md before "
                "trusting this skill in a new environment."
            )
        if bundle.line_count > 500:
            warnings.append(
                f"SKILL.md body is {bundle.line_count} lines; consider trimming before publishing."
            )
        if candidate.dependencies:
            warnings.append(
                "Depends on: " + ", ".join(candidate.dependencies) + " (generate/export them too)."
            )
        return warnings

    @staticmethod
    def _prune_dependency_warnings(skills: list[GeneratedSkill]) -> None:
        """Drop 'depends on X' warnings when X was generated in the same run."""
        generated = {skill.name for skill in skills}
        for skill in skills:
            dependencies = set(skill.candidate.dependencies) if skill.candidate else set()
            if dependencies and dependencies <= generated:
                skill.warnings = [
                    warning for warning in skill.warnings if not warning.startswith("Depends on:")
                ]
