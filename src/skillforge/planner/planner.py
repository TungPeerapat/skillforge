"""Skill planner orchestration."""

from __future__ import annotations

from datetime import UTC, datetime

from skillforge.logging import get_logger, trace_span
from skillforge.models import RepositoryProfile, SkillCandidate, SkillPlan
from skillforge.planner.context import PlanContext
from skillforge.planner.rules import DEFAULT_RULES, SUPPORTED_SKILLS, PlanRule

logger = get_logger("planner")


class SkillPlanner:
    """Recommend skills for a repository based on evidence-backed rules."""

    def __init__(self, rules: tuple[PlanRule, ...] | None = None) -> None:
        self._rules = rules if rules is not None else DEFAULT_RULES

    @property
    def supported_skills(self) -> tuple[str, ...]:
        return tuple(rule.skill for rule in self._rules)

    def plan(self, profile: RepositoryProfile) -> SkillPlan:
        context = PlanContext(profile=profile)
        candidates: list[SkillCandidate] = []
        skipped: list[str] = []
        notes: list[str] = []

        with trace_span("plan", repository=profile.name):
            for rule in self._rules:
                try:
                    candidate = rule.run(context)
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning(
                        "planner rule failed",
                        extra={"rule": rule.id, "error_type": type(exc).__name__},
                    )
                    skipped.append(f"{rule.skill}: rule failed ({type(exc).__name__})")
                    continue
                if candidate is None:
                    skipped.append(f"{rule.skill}: no supporting evidence")
                    continue
                candidates.append(candidate)

        candidates = self._resolve_dependencies(candidates, notes)
        candidates.sort(key=lambda item: (item.priority, item.name))
        if not candidates:
            notes.append(
                "No skills were recommended: the repository did not contain enough "
                "evidence-backed workflows. Check `skillforge analyze --json` for details."
            )
        logger.debug("planned skills", extra={"count": len(candidates)})
        return SkillPlan(
            repository=profile.name,
            generated_at=datetime.now(UTC),
            candidates=candidates,
            skipped=skipped,
            notes=notes,
        )

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _resolve_dependencies(
        candidates: list[SkillCandidate], notes: list[str]
    ) -> list[SkillCandidate]:
        """Annotate candidates whose dependencies were not themselves recommended."""
        available = {candidate.name for candidate in candidates}
        resolved: list[SkillCandidate] = []
        for candidate in candidates:
            missing = [name for name in candidate.dependencies if name not in available]
            if missing:
                candidate = candidate.model_copy(
                    update={
                        "notes": [
                            *candidate.notes,
                            "Depends on "
                            + ", ".join(missing)
                            + ", which was not recommended for this repository.",
                        ]
                    }
                )
                notes.append(
                    f"{candidate.name} depends on unavailable skill(s): {', '.join(missing)}"
                )
            resolved.append(candidate)
        return resolved


__all__ = ["SUPPORTED_SKILLS", "SkillPlanner"]
