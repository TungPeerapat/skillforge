"""Reading and writing portable skill bundles on disk.

Writes always go through :func:`skillforge.security.paths.safe_join`, so a skill
name or file path can never escape the output directory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from skillforge.errors import NotFoundError, SkillExistsError, SkillForgeError
from skillforge.models import (
    MANIFEST_FILENAME,
    GeneratedSkill,
    SkillBundle,
    SkillFile,
    SkillManifest,
    SkillMetadata,
)
from skillforge.security.paths import PathViolation, safe_join
from skillforge.utils.fs import atomic_write_text, read_json, read_text_capped
from skillforge.utils.markdown import split_frontmatter
from skillforge.utils.text import truncate

MAX_SKILL_FILE_BYTES = 512_000


@dataclass
class StoredSkill:
    """A skill directory found on disk."""

    directory: Path
    name: str
    bundle: SkillBundle
    manifest: SkillManifest | None = None
    metadata_error: str | None = None
    warnings: list[str] = field(default_factory=list)
    raw_skill_md: str = ""

    @property
    def generated_by_skillforge(self) -> bool:
        return self.manifest is not None

    @property
    def description(self) -> str:
        return self.bundle.metadata.description


def write_generated_skill(
    skill: GeneratedSkill,
    output_dir: Path,
    *,
    repository: str,
    git_commit: str | None = None,
    git_dirty: bool | None = None,
    overwrite: bool = False,
) -> Path:
    """Write one generated skill under ``output_dir/<name>`` and return its path."""
    manifest = skill.build_manifest(
        tool_version=skill.bundle.metadata.metadata.get("generator-version", ""),
        repository=repository,
        git_commit=git_commit,
        git_dirty=git_dirty,
    )
    target = _resolve_skill_dir(output_dir, skill.name)
    if target.exists() and any(target.iterdir()) and not overwrite:
        raise SkillExistsError(
            f"Skill directory already exists: {target}",
            hint="pass --force to overwrite, or run `skillforge clean` first",
        )
    outputs: dict[str, str] = skill.bundle.all_contents()
    outputs[MANIFEST_FILENAME] = manifest.model_dump_json(indent=2)
    for relative, content in outputs.items():
        _write_relative(target, relative, content)
    return target


def write_bundle(
    bundle: SkillBundle,
    target_dir: Path,
    *,
    manifest: SkillManifest | None = None,
    overwrite: bool = False,
) -> Path:
    """Write an arbitrary bundle (used by tests and tools)."""
    if target_dir.exists() and any(target_dir.iterdir()) and not overwrite:
        raise SkillExistsError(f"Skill directory already exists: {target_dir}")
    outputs = bundle.all_contents()
    if manifest is not None:
        outputs[MANIFEST_FILENAME] = manifest.model_dump_json(indent=2)
    for relative, content in outputs.items():
        _write_relative(target_dir, relative, content)
    return target_dir


def _resolve_skill_dir(output_dir: Path, name: str) -> Path:
    try:
        target = safe_join(output_dir, name)
    except PathViolation as exc:  # pragma: no cover - names are validated earlier
        raise SkillForgeError(f"Unsafe skill name: {name!r}") from exc
    if target == output_dir.resolve():  # pragma: no cover - guarded by validation
        raise SkillForgeError("Skill name must not be empty")
    return target


def _write_relative(root: Path, relative: str, content: str) -> None:
    try:
        target = safe_join(root, relative)
    except PathViolation as exc:
        raise SkillForgeError(f"Unsafe skill file path: {relative!r}") from exc
    atomic_write_text(target, content)


# ------------------------------------------------------------------ reading


def read_bundle(skill_dir: Path) -> StoredSkill:
    """Load a skill directory from disk into the in-memory representation."""
    if not skill_dir.is_dir():
        raise NotFoundError(f"Skill directory not found: {skill_dir}")
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        raise NotFoundError(
            f"No SKILL.md in {skill_dir}",
            hint="a skill directory must contain SKILL.md with YAML frontmatter",
        )
    text, _truncated = read_text_capped(skill_md, MAX_SKILL_FILE_BYTES)
    parsed = split_frontmatter(text)
    warnings: list[str] = []
    metadata_error: str | None = None
    metadata: SkillMetadata | None = None
    if parsed.error:
        metadata_error = parsed.error
    elif parsed.metadata is None:
        metadata_error = "SKILL.md has no YAML frontmatter"
    else:
        try:
            filtered = {
                key: value
                for key, value in parsed.metadata.items()
                if key
                in {"name", "description", "license", "compatibility", "allowed-tools", "metadata"}
            }
            metadata = SkillMetadata.model_validate(filtered)
        except Exception as exc:
            metadata_error = truncate(str(exc), 300)
    if metadata is None:
        fallback_name = skill_dir.name.lower()
        metadata = SkillMetadata(
            name=fallback_name if _looks_like_skill_name(fallback_name) else "invalid-skill-name",
            description="Invalid or missing frontmatter.",
        )
    files: dict[str, SkillFile] = {}
    for path in sorted(skill_dir.rglob("*")):
        if path.is_dir() or path.name in {"SKILL.md", MANIFEST_FILENAME}:
            continue
        if path.is_symlink():
            warnings.append(f"ignored symlink: {path.relative_to(skill_dir).as_posix()}")
            continue
        try:
            relative = path.resolve().relative_to(skill_dir.resolve()).as_posix()
        except ValueError:
            warnings.append(f"ignored path outside skill directory: {path}")
            continue
        content, truncated = read_text_capped(path, MAX_SKILL_FILE_BYTES)
        if truncated:
            warnings.append(f"file truncated for analysis: {relative}")
        files[relative] = SkillFile(
            path=relative,
            content=content,
            executable=relative.startswith("scripts/"),
        )
    manifest = _read_manifest(skill_dir)
    return StoredSkill(
        directory=skill_dir,
        name=metadata.name,
        bundle=SkillBundle(metadata=metadata, body=parsed.body, files=files),
        manifest=manifest,
        metadata_error=metadata_error,
        warnings=warnings,
        raw_skill_md=text,
    )


def _read_manifest(skill_dir: Path) -> SkillManifest | None:
    manifest_path = skill_dir / MANIFEST_FILENAME
    if not manifest_path.is_file():
        return None
    try:
        payload = read_json(manifest_path)
        if not isinstance(payload, dict):
            return None
        return SkillManifest.model_validate(payload)
    except (SkillForgeError, ValueError):
        return None


def list_skill_directories(output_dir: Path) -> list[Path]:
    """Return skill directories (those containing SKILL.md) in stable order."""
    if not output_dir.is_dir():
        return []
    found = [
        child
        for child in sorted(output_dir.iterdir())
        if child.is_dir() and (child / "SKILL.md").is_file()
    ]
    return found


def _looks_like_skill_name(name: str) -> bool:
    from skillforge.models import is_valid_skill_name

    return is_valid_skill_name(name)
