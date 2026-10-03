"""Deterministic evaluation checks."""

from __future__ import annotations

from dataclasses import dataclass, field

from skillforge.evaluation.scenarios import ScenarioCheck
from skillforge.models import GeneratedSkill, RepositoryProfile, ValidationResult
from skillforge.security.command_risk import classify_command
from skillforge.utils.markdown import fenced_code_blocks
from skillforge.utils.tokens import estimate_tokens


@dataclass
class EvalContext:
    """Everything the checks may look at for one scenario."""

    profile: RepositoryProfile
    skills: list[GeneratedSkill]
    validations: dict[str, ValidationResult] = field(default_factory=dict)

    def skill(self, name: str) -> GeneratedSkill | None:
        for skill in self.skills:
            if skill.name == name:
                return skill
        return None

    def selected(self, pattern: str) -> list[GeneratedSkill]:
        if pattern in ("", "*"):
            return list(self.skills)
        found = self.skill(pattern)
        return [found] if found is not None else []


@dataclass
class CheckResult:
    id: str
    description: str
    passed: bool | None
    detail: str = ""
    measured: bool = True


def _commands(skill: GeneratedSkill) -> list[str]:
    commands: list[str] = []
    for block in fenced_code_blocks(skill.bundle.body):
        for line in block.content.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                commands.append(stripped)
    return commands


def run_check(check: ScenarioCheck, context: EvalContext) -> CheckResult:
    """Execute one check and return its outcome."""
    check_id = f"{check.type}:{check.skill}:{check.value or check.contains}".rstrip(":")
    skill_scope = context.selected(check.skill)

    if check.type == "skill_generated":
        name = check.value or check.skill
        found = context.skill(name) is not None
        return CheckResult(check_id, f"skill '{name}' was generated", found)

    if check.type == "validation_passes":
        targets = skill_scope
        if not targets:
            return CheckResult(check_id, "no skills to validate", False)
        failures: list[str] = []
        for skill in targets:
            result = context.validations.get(skill.name)
            if result is None or not result.ok:
                failures.append(skill.name)
        return CheckResult(
            check_id,
            "generated skills pass validation",
            not failures,
            detail="failed: " + ", ".join(failures) if failures else "",
        )

    if check.type == "no_dangerous_commands":
        offenders: list[str] = []
        for skill in skill_scope:
            for command in _commands(skill):
                if classify_command(command).is_dangerous:
                    offenders.append(f"{skill.name}: {command}")
        return CheckResult(
            check_id,
            "no destructive commands embedded in skills",
            not offenders,
            detail="; ".join(offenders[:5]),
        )

    if check.type == "command_present":
        needle = check.contains or check.value
        for skill in skill_scope:
            if any(needle in command for command in _commands(skill)):
                return CheckResult(check_id, f"command containing '{needle}' present", True)
        return CheckResult(
            check_id,
            f"command containing '{needle}' present",
            False,
            detail=f"checked {len(skill_scope)} skill(s)",
        )

    if check.type == "command_with_evidence":
        needle = check.contains or check.value
        known = [command.command for command in context.profile.commands]
        matches = [command for command in known if needle in command]
        if not matches:
            return CheckResult(
                check_id,
                f"'{needle}' is backed by repository evidence",
                False,
                detail="no analysis command matched",
            )
        evidenced = all(
            command.evidence for command in context.profile.commands if command.command in matches
        )
        return CheckResult(
            check_id,
            f"'{needle}' is backed by repository evidence",
            evidenced,
            detail=f"{len(matches)} matching command(s)",
        )

    if check.type == "workflow_present":
        wanted = check.value or check.contains
        found = any(
            workflow.category.value == wanted or workflow.id == wanted
            for workflow in context.profile.workflows
        )
        return CheckResult(check_id, f"workflow '{wanted}' detected", found)

    if check.type == "technology_detected":
        wanted = check.value or check.contains
        found = context.profile.has_technology(wanted)
        return CheckResult(check_id, f"technology '{wanted}' detected", found)

    if check.type == "risk_detected":
        wanted = check.value or check.contains
        found = any(wanted in risk.id for risk in context.profile.risks)
        return CheckResult(check_id, f"risk '{wanted}' detected", found)

    if check.type == "unknown_recorded":
        wanted = check.value or check.contains
        found = any(wanted in unknown.id for unknown in context.profile.unknowns)
        return CheckResult(check_id, f"unknown '{wanted}' recorded", found)

    if check.type == "skill_md_token_budget":
        over: list[str] = []
        for skill in skill_scope:
            tokens = estimate_tokens(skill.bundle.body)
            if tokens > check.max_tokens:
                over.append(f"{skill.name} ({tokens} tokens)")
        return CheckResult(
            check_id,
            f"SKILL.md bodies stay under {check.max_tokens} tokens",
            not over,
            detail=", ".join(over),
        )

    return CheckResult(
        check_id, f"unknown check type '{check.type}'", None, measured=False
    )  # pragma: no cover
