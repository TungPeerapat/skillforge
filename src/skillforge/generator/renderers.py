"""Render blueprints into skill bundles (SKILL.md body + references + scripts)."""

from __future__ import annotations

from dataclasses import dataclass

from skillforge.errors import ExampleError
from skillforge.generator.blueprint import CommandView, SkillBlueprint
from skillforge.generator.references import (
    architecture_reference,
    commands_reference,
    environment_reference,
    evidence_reference,
    workflows_reference,
)
from skillforge.generator.scripts import (
    _default_base_url,
    scripts_for_skill,
)
from skillforge.generator.templates import render_template
from skillforge.models import SkillFile

_TEMPLATES: dict[str, str] = {
    "project-runner": "skills/project_runner.md.j2",
    "project-builder": "skills/project_builder.md.j2",
    "test-runner": "skills/test_runner.md.j2",
    "project-debugger": "skills/project_debugger.md.j2",
    "database-debugger": "skills/database_debugger.md.j2",
    "migration-guardian": "skills/migration_guardian.md.j2",
    "api-contract-checker": "skills/api_contract_checker.md.j2",
    "code-reviewer": "skills/code_reviewer.md.j2",
    "release-verifier": "skills/release_verifier.md.j2",
}

_WORKFLOW_REFERENCE_SKILLS = frozenset(
    {"project-runner", "project-debugger", "test-runner", "release-verifier"}
)


@dataclass(frozen=True)
class RenderContext:
    """Values handed to Jinja templates alongside the blueprint."""

    primary: CommandView | None
    steps: tuple[CommandView, ...]
    scripts: tuple[SkillFile, ...]
    references: tuple[SkillFile, ...]
    base_url: str


def _primary_command(skill: str, blueprint: SkillBlueprint) -> CommandView | None:
    for command in blueprint.commands:
        if skill in {"project-runner", "project-debugger"} and command.purpose == "run":
            return command
        if skill == "project-builder" and command.purpose == "build":
            return command
        if skill == "test-runner" and command.purpose == "test":
            return command
        if skill == "migration-guardian" and command.purpose == "migrate":
            return command
    return blueprint.commands[0] if blueprint.commands else None


def _references_for(skill: str, blueprint: SkillBlueprint) -> list[SkillFile]:
    references: list[SkillFile] = []
    commands = commands_reference(blueprint)
    if commands is not None:
        references.append(commands)
    if skill in _WORKFLOW_REFERENCE_SKILLS:
        workflows = workflows_reference(blueprint)
        if workflows is not None:
            references.append(workflows)
    architecture = architecture_reference(blueprint)
    if architecture is not None:
        references.append(architecture)
    environment = environment_reference(blueprint)
    if environment is not None:
        references.append(environment)
    references.append(evidence_reference(blueprint))
    return references


def render_skill(
    skill: str, blueprint: SkillBlueprint
) -> tuple[str, list[SkillFile], list[SkillFile]]:
    """Render the SKILL.md body plus supporting files for one skill."""
    template_path = _TEMPLATES.get(skill)
    if template_path is None:
        raise ExampleError(f"No renderer is registered for skill '{skill}'")
    scripts = scripts_for_skill(skill, blueprint)
    references = _references_for(skill, blueprint)
    context = RenderContext(
        primary=_primary_command(skill, blueprint),
        steps=tuple(blueprint.commands),
        scripts=tuple(scripts),
        references=tuple(references),
        base_url=_default_base_url(blueprint),
    )
    body = render_template(template_path, bp=blueprint, ctx=context).strip() + "\n"
    return body, references, scripts
