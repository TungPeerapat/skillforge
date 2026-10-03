"""Exporter implementations.

Each exporter targets the documented skill layout of one agent or of the
portable Agent Skills standard:

* Claude Code — ``.claude/skills/<name>/SKILL.md`` (project) or
  ``~/.claude/skills`` (personal).
* OpenAI Codex — ``.agents/skills/<name>/SKILL.md`` (repository root); Codex
  also reads skills from ``$HOME/.agents/skills``.
* OpenCode — ``.opencode/skills/<name>/SKILL.md`` (project) or
  ``~/.config/opencode/skills`` (global). OpenCode additionally reads
  ``.claude/skills`` and ``.agents/skills``.
* Portable — ``skills/<name>/SKILL.md`` following the Agent Skills standard.

All four accept the same SKILL.md frontmatter fields SkillForge emits
(``name``, ``description``, ``license``, ``compatibility``, ``metadata``).
Exporters exist for the *directory layout*, not because the content differs.
"""

from __future__ import annotations

from pathlib import Path

from skillforge.exporters.base import BaseExporter


class ClaudeCodeExporter(BaseExporter):
    """Project/personal layout used by Claude Code."""

    id = "claude"
    display_name = "Claude Code"
    description = "Writes .claude/skills/<name>/ (or ~/.claude/skills with --global)."
    skills_subdir = ".claude/skills"

    def skills_root(self, repo_root: Path) -> Path:
        if self._global_scope:
            return Path.home() / ".claude" / "skills"
        return repo_root / ".claude" / "skills"

    def compatibility_notes(self) -> list[str]:
        return [
            "Claude Code loads project skills from .claude/skills/<name>/SKILL.md and personal "
            "skills from ~/.claude/skills.",
            "Claude Code also understands the optional allowed-tools field; SkillForge does not "
            "set it because tool restrictions are target-specific.",
        ]


class CodexExporter(BaseExporter):
    """Repository layout used by OpenAI Codex CLI/IDE/app."""

    id = "codex"
    display_name = "OpenAI Codex"
    description = "Writes .agents/skills/<name>/ (or ~/.agents/skills with --global)."
    skills_subdir = ".agents/skills"

    def skills_root(self, repo_root: Path) -> Path:
        if self._global_scope:
            return Path.home() / ".agents" / "skills"
        return repo_root / ".agents" / "skills"

    def compatibility_notes(self) -> list[str]:
        return [
            "Codex discovers repository skills under .agents/skills and personal skills under "
            "~/.agents/skills, using the same SKILL.md format.",
            "AGENTS.md is Codex's standing-instructions file; SkillForge does not generate or "
            "modify it.",
        ]


class OpenCodeExporter(BaseExporter):
    """Project/global layout used by OpenCode."""

    id = "opencode"
    display_name = "OpenCode"
    description = "Writes .opencode/skills/<name>/ (or ~/.config/opencode/skills with --global)."
    skills_subdir = ".opencode/skills"

    def skills_root(self, repo_root: Path) -> Path:
        if self._global_scope:
            return Path.home() / ".config" / "opencode" / "skills"
        return repo_root / ".opencode" / "skills"

    def compatibility_notes(self) -> list[str]:
        return [
            "OpenCode requires SKILL.md in all caps and a name matching the directory.",
            "OpenCode ignores frontmatter fields it does not know; SkillForge emits only "
            "name, description, license, compatibility, and metadata.",
        ]


class PortableExporter(BaseExporter):
    """Agent-neutral layout following the Agent Skills standard."""

    id = "portable"
    display_name = "Portable skills"
    description = "Writes skills/<name>/ at the repository root, usable by any agent."
    skills_subdir = "skills"

    def skills_root(self, repo_root: Path) -> Path:
        return repo_root / "skills"

    def compatibility_notes(self) -> list[str]:
        return [
            "The Agent Skills standard defines SKILL.md plus optional scripts/, references/, and "
            "assets/ directories; this layout is portable across conforming agents.",
        ]
