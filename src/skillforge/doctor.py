"""``skillforge doctor`` — environment inspection.

Only checks fixed, well-known executables with hardcoded arguments. Repository
commands are never executed, and API key values are never printed.
"""

from __future__ import annotations

import platform
import sys
from datetime import UTC, datetime
from pathlib import Path

from skillforge import __version__
from skillforge.config import find_project_config, user_config_path
from skillforge.logging import get_logger
from skillforge.models import CheckStatus, DoctorReport, ToolCheck
from skillforge.skills import list_skill_directories
from skillforge.utils.subproc import probe

logger = get_logger("doctor")

#: (name, argv, category, required) — argv is a literal list, never built from input.
_TOOLS: tuple[tuple[str, list[str], str, bool], ...] = (
    ("Python", [sys.executable, "--version"], "tools", True),
    ("Git", ["git", "--version"], "tools", True),
    ("Docker", ["docker", "--version"], "tools", False),
    ("Docker Compose", ["docker", "compose", "version"], "tools", False),
    ("Node.js", ["node", "--version"], "tools", False),
    ("npm", ["npm", "--version"], "tools", False),
    ("pnpm", ["pnpm", "--version"], "tools", False),
    ("Yarn", ["yarn", "--version"], "tools", False),
    ("uv", ["uv", "--version"], "tools", False),
    ("Poetry", ["poetry", "--version"], "tools", False),
    (".NET SDK", ["dotnet", "--version"], "tools", False),
    ("Go", ["go", "version"], "tools", False),
    ("Flutter", ["flutter", "--version"], "tools", False),
    ("Java", ["java", "-version"], "tools", False),
    ("Make", ["make", "--version"], "tools", False),
)

#: (name, argv) for coding agents SkillForge can export to.
_AGENTS: tuple[tuple[str, list[str]], ...] = (
    ("Claude Code", ["claude", "--version"]),
    ("OpenAI Codex", ["codex", "--version"]),
    ("OpenCode", ["opencode", "--version"]),
)


def inspect_environment(repo_root: Path | None = None) -> DoctorReport:
    """Collect environment facts without executing repository content."""
    checks: list[ToolCheck] = []
    python_version = platform.python_version()
    checks.append(
        ToolCheck(
            name="Python",
            category="runtime",
            status=CheckStatus.OK,
            version=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            path=sys.executable,
            detail="running this process",
            required=True,
        )
    )
    checks.append(
        ToolCheck(
            name="SkillForge",
            category="runtime",
            status=CheckStatus.OK,
            version=__version__,
            detail=f"Python {python_version} on {platform.system()}",
            required=True,
        )
    )

    for name, argv, category, required in _TOOLS:
        if name == "Python":
            continue  # already reported above
        result = probe(argv)
        status = (
            CheckStatus.OK if result.ok else (CheckStatus.FAIL if required else CheckStatus.MISSING)
        )
        checks.append(
            ToolCheck(
                name=name,
                category=category,
                status=status,
                version=result.version_line[:80] if result.ok else "",
                path=result.path or "",
                detail="" if result.ok else (result.error or "not found"),
                required=required,
            )
        )

    for name, argv in _AGENTS:
        result = probe(argv)
        checks.append(
            ToolCheck(
                name=name,
                category="agents",
                status=CheckStatus.OK if result.ok else CheckStatus.MISSING,
                version=result.version_line[:60] if result.ok else "",
                path=result.path or "",
                detail="" if result.ok else "not installed (optional)",
            )
        )

    config_source: str | None = None
    config_issues: list[str] = []
    skill_output = ""
    generated = 0
    if repo_root is not None:
        project_config = find_project_config(repo_root)
        user_config = user_config_path()
        if project_config is not None:
            config_source = str(project_config)
        elif user_config.is_file():
            config_source = str(user_config)
        try:
            from skillforge.config import load_settings

            settings = load_settings(repo_root, env={})
            output_dir = settings.output_dir(repo_root)
            skill_output = str(output_dir)
            generated = len(list_skill_directories(output_dir))
        except Exception as exc:
            config_issues.append(str(exc))

    checks.append(
        ToolCheck(
            name="httpx (LLM extra)",
            category="providers",
            status=_optional_import_status("httpx"),
            detail="required for HTTP providers",
        )
    )
    try:
        from skillforge.config import load_settings
        from skillforge.providers.registry import provider_status

        if repo_root is not None:
            loaded = load_settings(repo_root, env={})
            for provider_state in provider_status(loaded):
                checks.append(
                    ToolCheck(
                        name=f"provider: {provider_state.id}",
                        category="providers",
                        status=(
                            CheckStatus.OK
                            if provider_state.available
                            else (
                                CheckStatus.WARN
                                if provider_state.configured
                                else CheckStatus.MISSING
                            )
                        ),
                        detail=provider_state.detail,
                    )
                )
    except Exception as exc:
        config_issues.append(f"provider check failed: {exc}")

    report = DoctorReport(
        tool_version=__version__,
        generated_at=datetime.now(UTC),
        python_version=python_version,
        platform=f"{platform.system()} {platform.release()} ({platform.machine()})",
        checks=checks,
        config_source=config_source,
        config_issues=config_issues,
        skill_output=skill_output,
        generated_skills=generated,
    )
    return report


def _optional_import_status(module: str) -> CheckStatus:
    try:
        __import__(module)
    except ImportError:
        return CheckStatus.WARN
    return CheckStatus.OK


__all__ = ["inspect_environment"]
