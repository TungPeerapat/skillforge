"""Installed-artifact locators (K2).

Read-only, symlink-safe, bounded. Only metadata files are read
(``*.dist-info/METADATA``, ``node_modules/<name>/package.json``); packages are
never imported or executed and locators never follow paths outside the
repository root. Global caches outside the project are intentionally not
searched: a version that is not provably local is reported as unresolved rather
than borrowed from an unrelated environment.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

_METADATA_READ_BYTES = 16_384
_PACKAGE_JSON_MAX_BYTES = 1_000_000
_DEFAULT_MAX_ARTIFACTS = 5_000
_ENV_DIRS = (".venv", "venv", "env", ".env")


def role_for_url_label(label: str) -> str:
    """Map a free-form URL label to a declared-URL role."""
    lowered = label.lower()
    if "doc" in lowered:
        return "documentation"
    if "source" in lowered or "repo" in lowered:
        return "repository"
    if "change" in lowered or "release" in lowered or "history" in lowered:
        return "changelog"
    if "bug" in lowered or "issue" in lowered:
        return "issues"
    if "home" in lowered:
        return "homepage"
    return "metadata"


def clean_repository_url(url: str) -> str:
    """Normalize ``git+https://…/.git`` forms into a fetchable URL."""
    cleaned = url.strip()
    if cleaned.startswith("git+"):
        cleaned = cleaned[4:]
    if cleaned.endswith(".git"):
        cleaned = cleaned[: -len(".git")]
    return cleaned


@dataclass(frozen=True, slots=True)
class InstalledArtifact:
    """One locally installed package version."""

    ecosystem: str
    name: str
    version: str
    path: str
    source: str = "installed_artifact"
    urls: dict[str, list[str]] = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, str]:
        return (self.ecosystem, self.name.lower())


@dataclass
class InstalledIndex:
    """All artifacts found locally, keyed by ``(ecosystem, name.lower())``."""

    artifacts: dict[tuple[str, str], InstalledArtifact] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _read_text_capped(path: Path, limit: int) -> str | None:
    try:
        with path.open("rb") as handle:
            data = handle.read(limit)
    except OSError:
        return None
    return data.decode("utf-8", errors="replace")


def _add_declared_url(urls: dict[str, list[str]], role: str, url: object) -> None:
    if not isinstance(url, str) or not url.strip():
        return
    cleaned = clean_repository_url(url) if role == "repository" else url.strip()
    if cleaned not in urls.setdefault(role, []):
        urls[role].append(cleaned)


def _parse_dist_info_metadata(
    text: str,
) -> tuple[str, str, dict[str, list[str]]] | None:
    name = ""
    version = ""
    urls: dict[str, list[str]] = {}
    for line in text.splitlines():
        if not line:
            break
        if line.startswith("Name:"):
            name = line[5:].strip()
        elif line.startswith("Version:"):
            version = line[8:].strip()
        elif line.startswith("Home-page:"):
            _add_declared_url(urls, "homepage", line[10:])
        elif line.startswith("Project-URL:"):
            payload = line[12:].strip()
            label, _, url = payload.partition(",")
            if url.strip():
                _add_declared_url(urls, role_for_url_label(label), url)
            else:
                _add_declared_url(urls, "homepage", label)
    if not name or not version:
        return None
    return name, version, urls


def _iter_python_environments(root: Path) -> Iterator[Path]:
    for env_name in _ENV_DIRS:
        base = root / env_name
        if not base.is_dir() or base.is_symlink():
            continue
        candidates = [base / "Lib" / "site-packages"]
        candidates.extend(sorted(base.glob("lib*/python*/site-packages")))
        for candidate in candidates:
            if candidate.is_dir() and not candidate.is_symlink():
                yield candidate


def _collect_python(root: Path, index: InstalledIndex, limit: int) -> None:
    for site_packages in _iter_python_environments(root):
        try:
            dist_infos = sorted(site_packages.glob("*.dist-info"))
        except OSError:  # pragma: no cover - unreadable directory
            continue
        for dist_info in dist_infos:
            if len(index.artifacts) >= limit:
                index.warnings.append(
                    f"installed-artifact scan capped at {limit} packages; "
                    "some versions may stay unresolved"
                )
                return
            if dist_info.is_symlink():
                continue
            metadata = dist_info / "METADATA"
            if metadata.is_symlink() or not metadata.is_file():
                continue
            text = _read_text_capped(metadata, _METADATA_READ_BYTES)
            if text is None:
                continue
            parsed = _parse_dist_info_metadata(text)
            if parsed is None:
                continue
            name, version, urls = parsed
            try:
                relative = dist_info.relative_to(root).as_posix()
            except ValueError:  # pragma: no cover - defensive
                relative = dist_info.as_posix()
            artifact = InstalledArtifact("pypi", name, version, relative, urls=urls)
            index.artifacts.setdefault(artifact.key, artifact)


def _read_package_json(directory: Path, fallback_name: str, root: Path) -> InstalledArtifact | None:
    package_json = directory / "package.json"
    if package_json.is_symlink() or not package_json.is_file():
        return None
    text = _read_text_capped(package_json, _PACKAGE_JSON_MAX_BYTES)
    if text is None:
        return None
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    version = data.get("version")
    if not isinstance(version, str) or not version.strip():
        return None
    name = data.get("name") if isinstance(data.get("name"), str) else fallback_name
    urls: dict[str, list[str]] = {}
    _add_declared_url(urls, "homepage", data.get("homepage"))
    repository = data.get("repository")
    if isinstance(repository, dict):
        repository = repository.get("url")
    _add_declared_url(urls, "repository", repository)
    bugs = data.get("bugs")
    if isinstance(bugs, dict):
        bugs = bugs.get("url")
    _add_declared_url(urls, "issues", bugs)
    _add_declared_url(urls, "documentation", data.get("documentation"))
    try:
        relative = directory.relative_to(root).as_posix()
    except ValueError:  # pragma: no cover - defensive
        relative = directory.as_posix()
    return InstalledArtifact("npm", str(name), version.strip(), relative, urls=urls)


def _node_modules_dirs(root: Path, component_paths: tuple[str, ...]) -> Iterator[Path]:
    candidates = [root / "node_modules"]
    for component in component_paths:
        cleaned = component.strip("/")
        if cleaned and cleaned != ".":
            candidates.append(root / cleaned / "node_modules")
    for candidate in candidates:
        if candidate.is_dir() and not candidate.is_symlink():
            yield candidate


def _collect_node(
    root: Path, component_paths: tuple[str, ...], index: InstalledIndex, limit: int
) -> None:
    for node_modules in _node_modules_dirs(root, component_paths):
        try:
            children = sorted(node_modules.iterdir(), key=lambda item: item.name.lower())
        except OSError:  # pragma: no cover - unreadable directory
            continue
        for child in children:
            if len(index.artifacts) >= limit:
                index.warnings.append(
                    f"installed-artifact scan capped at {limit} packages; "
                    "some versions may stay unresolved"
                )
                return
            if child.is_symlink() or child.name.startswith("."):
                continue
            if child.name.startswith("@"):
                if not child.is_dir():
                    continue
                try:
                    scoped = sorted(child.iterdir(), key=lambda item: item.name.lower())
                except OSError:  # pragma: no cover
                    continue
                for package in scoped:
                    if package.is_symlink() or not package.is_dir() or package.name.startswith("."):
                        continue
                    artifact = _read_package_json(package, f"{child.name}/{package.name}", root)
                    if artifact is not None:
                        index.artifacts.setdefault(artifact.key, artifact)
                continue
            if not child.is_dir() or child.name in {"node_modules"}:
                continue
            artifact = _read_package_json(child, child.name, root)
            if artifact is not None:
                index.artifacts.setdefault(artifact.key, artifact)


def collect_installed(
    root: Path,
    *,
    component_paths: tuple[str, ...] = (),
    max_artifacts: int = _DEFAULT_MAX_ARTIFACTS,
) -> InstalledIndex:
    """Locate locally installed package versions under ``root``."""
    index = InstalledIndex()
    resolved = root.resolve()
    _collect_python(resolved, index, max_artifacts)
    _collect_node(resolved, component_paths, index, max_artifacts)
    return index


__all__ = [
    "InstalledArtifact",
    "InstalledIndex",
    "collect_installed",
]
