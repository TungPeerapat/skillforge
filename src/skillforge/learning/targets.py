"""Learning target registry.

v0.2 implements exactly one target, ``project-runner``. The registry exists so
future releases can add ``project-builder``, ``test-runner``,
``migration-guardian``, and ``release-verifier`` without changing the session
engine; nothing else is implemented or advertised yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from skillforge.errors import UsageError
from skillforge.learning.planning import PlanOptions, build_execution_plan
from skillforge.learning.upgrade import SKILL_NAME, upgrade_skill
from skillforge.models import (
    ExecutionPlan,
    LearnedWorkflow,
    LearningSession,
    RepositoryProfile,
    UpgradeResult,
)


@dataclass(frozen=True)
class LearningTarget:
    """Everything the session engine needs to know about one learning target."""

    id: str
    title: str
    description: str
    skill_name: str

    def build_plan(
        self,
        profile: RepositoryProfile,
        *,
        scan_contents: dict[str, str],
        options: PlanOptions | None = None,
    ) -> ExecutionPlan:
        return build_execution_plan(
            profile, scan_contents=scan_contents, options=options, target=self.id
        )

    def upgrade(
        self,
        skill_dir: Path,
        *,
        session: LearningSession,
        workflow: LearnedWorkflow,
        force: bool = False,
    ) -> UpgradeResult:
        return upgrade_skill(skill_dir, session=session, workflow=workflow, force=force)


_PROJECT_RUNNER = LearningTarget(
    id="project-runner",
    title="Project runner",
    description=(
        "Learn how the application is started, verify it with controlled execution, "
        "and upgrade the project-runner skill with the verified workflow."
    ),
    skill_name=SKILL_NAME,
)

#: Registered targets. Only project-runner is implemented in v0.2.
TARGETS: dict[str, LearningTarget] = {_PROJECT_RUNNER.id: _PROJECT_RUNNER}


def target_ids() -> list[str]:
    return sorted(TARGETS)


def get_target(target_id: str) -> LearningTarget:
    normalized = (target_id or "").strip().lower()
    if normalized not in TARGETS:
        raise UsageError(
            f"unsupported learning target: '{target_id}'",
            hint="v0.2 supports: " + ", ".join(target_ids()),
        )
    return TARGETS[normalized]


def looks_like_target(value: str) -> bool:
    return (value or "").strip().lower() in TARGETS


__all__ = ["TARGETS", "LearningTarget", "get_target", "looks_like_target", "target_ids"]
