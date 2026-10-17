"""Global versioned knowledge store (K4).

Data, not config: the store lives in the platform data directory
(``%LOCALAPPDATA%\\skillforge\\knowledge`` on Windows,
``${XDG_DATA_HOME:-~/.local/share}/skillforge/knowledge`` on POSIX) with an
optional ``SKILLFORGE_KNOWLEDGE_HOME`` override.

Layout::

    <home>/
      registry.json
      locks/
      <ecosystem>/<tech-slug>/<version>/
        manifest.json
        sources.json
        capabilities.jsonl
        relationships.jsonl
        domains.json
        coverage.json
        conflicts.json
        cache/sha256/<ab>/<hash>.json      # K7
        index/                             # K5/K14
        compiled/<store_hash>.json         # reuse marker

Rules: atomic writes, bounded file locks with stale-lock recovery, hash
verification on read, corruption quarantined (``.corrupt-<ts>``) instead of
crashing, content-derived ``store_hash`` so regenerating identical knowledge
reuses the same entry.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from skillforge.errors import SkillForgeError
from skillforge.knowledge.versions import normalize_version
from skillforge.models import (
    KnowledgeManifest,
    KnowledgeRegistry,
    KnowledgeRegistryEntry,
    KnowledgeSource,
    KnowledgeTarget,
)
from skillforge.security.paths import PathViolation, safe_join
from skillforge.utils.fs import atomic_write_text, ensure_directory, sha256_file, sha256_text

logger = logging.getLogger(__name__)

REGISTRY_FILENAME = "registry.json"
MANIFEST_FILENAME = "manifest.json"
SOURCES_FILENAME = "sources.json"
CAPABILITIES_FILENAME = "capabilities.jsonl"
RELATIONSHIPS_FILENAME = "relationships.jsonl"
DOMAINS_FILENAME = "domains.json"
COVERAGE_FILENAME = "coverage.json"
CONFLICTS_FILENAME = "conflicts.json"
COMPILED_DIRNAME = "compiled"

CONTENT_FILENAMES: tuple[str, ...] = (
    SOURCES_FILENAME,
    CAPABILITIES_FILENAME,
    RELATIONSHIPS_FILENAME,
    DOMAINS_FILENAME,
    COVERAGE_FILENAME,
    CONFLICTS_FILENAME,
)

DEFAULT_LOCK_TIMEOUT = 10.0
LOCK_STALE_SECONDS = 600.0
STORE_SCHEMA_VERSION = 1


class StoreError(SkillForgeError):
    """Base class for knowledge-store failures."""


class StoreLockedError(StoreError):
    """Another writer holds the lock and the bounded wait expired."""


class StoreCorruptedError(StoreError):
    """Stored JSON is missing, unreadable, or fails hash verification."""


def knowledge_home(env: Mapping[str, str] | None = None, *, platform: str | None = None) -> Path:
    """Resolve the global knowledge home (platform data dir + override)."""
    environment = os.environ if env is None else env
    override = environment.get("SKILLFORGE_KNOWLEDGE_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    selected_platform = platform or sys.platform
    if selected_platform == "win32":
        base = environment.get("LOCALAPPDATA") or environment.get("APPDATA")
        if base:
            return Path(base) / "skillforge" / "knowledge"
        return Path.home() / "AppData" / "Local" / "skillforge" / "knowledge"
    if selected_platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "skillforge" / "knowledge"
    base = environment.get("XDG_DATA_HOME", "").strip()
    if base and Path(base).is_absolute():
        return Path(base) / "skillforge" / "knowledge"
    return Path.home() / ".local" / "share" / "skillforge" / "knowledge"


def technology_slug(technology: str) -> str:
    slug = re.sub(r"[^a-z0-9._-]+", "-", technology.lower()).strip("-")
    return slug or "unknown"


def normalized_store_version(ecosystem: str, version: str) -> str:
    return normalize_version(ecosystem, version) or version.strip()


@dataclass
class KnowledgeEntry:
    """Everything stored for one ``(ecosystem, technology, version)`` entry."""

    manifest: KnowledgeManifest
    sources: list[KnowledgeSource] = field(default_factory=list)
    capabilities: list[dict[str, Any]] = field(default_factory=list)
    relationships: list[dict[str, Any]] = field(default_factory=list)
    domains: dict[str, Any] = field(default_factory=dict)
    coverage: dict[str, int] = field(default_factory=dict)
    conflicts: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class StoreValidation:
    """Result of verifying one stored entry against its manifest hashes."""

    ok: bool
    issues: list[str] = field(default_factory=list)


def _json_dump(payload: object) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n"


def _jsonl_dump(records: Sequence[Mapping[str, Any]]) -> str:
    ordered = sorted(records, key=lambda item: str(item.get("id", "")))
    return "".join(json.dumps(item, sort_keys=True, default=str) + "\n" for item in ordered)


def _lock_name(key: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9._-]", ".", key)
    if len(name) > 120:
        name = f"{name[:80]}-{sha256_text(key)[:12]}"
    return name


class KnowledgeStore:
    """Read/write/lock/validate access to the versioned knowledge store."""

    def __init__(
        self,
        home: Path | None = None,
        *,
        lock_timeout: float = DEFAULT_LOCK_TIMEOUT,
        lock_stale_seconds: float = LOCK_STALE_SECONDS,
    ) -> None:
        self.home = Path(home) if home is not None else knowledge_home()
        self.lock_timeout = lock_timeout
        self.lock_stale_seconds = lock_stale_seconds

    # ------------------------------------------------------------------ paths
    def key_for(self, target: KnowledgeTarget) -> str:
        version = normalized_store_version(target.ecosystem, target.version)
        for component in (target.ecosystem, version):
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+_-]*", component):
                raise PathViolation("unsafe knowledge store key component")
        return f"{target.ecosystem.lower()}/{technology_slug(target.technology)}/{version}"

    def entry_dir(self, target: KnowledgeTarget) -> Path:
        return safe_join(self.home, self.key_for(target))

    def manifest_path(self, target: KnowledgeTarget) -> Path:
        return safe_join(self.entry_dir(target), MANIFEST_FILENAME)

    def exists(self, target: KnowledgeTarget) -> bool:
        return self.manifest_path(target).is_file()

    # ------------------------------------------------------------------- lock
    @contextmanager
    def lock(
        self,
        name: str,
        *,
        timeout: float | None = None,
        stale_after: float | None = None,
    ) -> Iterator[None]:
        """Bounded exclusive lock; stale locks (crashed writers) are recovered."""
        locks_dir = ensure_directory(self.home / "locks")
        lock_dir = safe_join(locks_dir, f"{_lock_name(name)}.lock")
        deadline = time.monotonic() + (timeout if timeout is not None else self.lock_timeout)
        stale = stale_after if stale_after is not None else self.lock_stale_seconds
        while True:
            try:
                lock_dir.mkdir(parents=False, exist_ok=False)
                break
            except FileExistsError:
                if self._lock_is_stale(lock_dir, stale):
                    shutil.rmtree(lock_dir, ignore_errors=True)
                    continue
                if time.monotonic() >= deadline:
                    raise StoreLockedError(
                        f"knowledge store lock '{name}' is held by another writer",
                        hint="retry later; stale locks are recovered automatically",
                    ) from None
                time.sleep(0.05)
        try:
            atomic_write_text(
                lock_dir / "owner.json",
                _json_dump(
                    {
                        "name": name,
                        "pid": os.getpid(),
                        "created_at": datetime.now(UTC).isoformat(),
                    }
                ),
            )
            yield
        finally:
            shutil.rmtree(lock_dir, ignore_errors=True)

    def _lock_is_stale(self, lock_dir: Path, stale_after: float) -> bool:
        owner = lock_dir / "owner.json"
        try:
            data = json.loads(owner.read_text(encoding="utf-8"))
            created = datetime.fromisoformat(str(data.get("created_at", "")))
            age = (datetime.now(UTC) - created).total_seconds()
            return age > stale_after
        except (OSError, ValueError, TypeError):
            try:
                return (time.time() - lock_dir.stat().st_mtime) > stale_after
            except OSError:  # pragma: no cover - lock vanished
                return True

    # ------------------------------------------------------------------ write
    def write_entry(self, target: KnowledgeTarget, entry: KnowledgeEntry) -> KnowledgeManifest:
        """Atomically store one entry and upsert the registry."""
        key = self.key_for(target)
        version = normalized_store_version(target.ecosystem, target.version)
        with self.lock(key):
            entry_dir = ensure_directory(self.entry_dir(target))
            payloads: dict[str, str] = {
                SOURCES_FILENAME: _json_dump(
                    [source.model_dump(mode="json") for source in entry.sources]
                ),
                CAPABILITIES_FILENAME: _jsonl_dump(entry.capabilities),
                RELATIONSHIPS_FILENAME: _jsonl_dump(entry.relationships),
                DOMAINS_FILENAME: _json_dump(entry.domains),
                COVERAGE_FILENAME: _json_dump(entry.coverage),
                CONFLICTS_FILENAME: _json_dump(entry.conflicts),
            }
            for filename, text in payloads.items():
                atomic_write_text(entry_dir / filename, text)

            manifest = entry.manifest.model_copy(
                update={
                    "technology": target.technology,
                    "ecosystem": target.ecosystem,
                    "resolved_version": version,
                    "capability_count": len(entry.capabilities),
                    "domain_count": len(entry.domains) or entry.manifest.domain_count,
                    "source_hashes": {
                        filename: sha256_text(text) for filename, text in payloads.items()
                    },
                }
            )
            manifest_text = _json_dump(manifest.model_dump(mode="json"))
            atomic_write_text(entry_dir / MANIFEST_FILENAME, manifest_text)

            compiled_dir = ensure_directory(entry_dir / COMPILED_DIRNAME)
            atomic_write_text(
                compiled_dir / f"{manifest.store_hash}.json",
                _json_dump(
                    {
                        "schema_version": STORE_SCHEMA_VERSION,
                        "store_hash": manifest.store_hash,
                        "manifest_hash": sha256_text(manifest_text),
                        "tool_version": manifest.tool_version,
                        "generated_at": manifest.generated_at.isoformat(),
                    }
                ),
            )
            self._upsert_registry(
                KnowledgeRegistryEntry(
                    key=key,
                    ecosystem=target.ecosystem,
                    technology=target.technology,
                    version=version,
                    store_hash=manifest.store_hash,
                    manifest_hash=sha256_text(manifest_text),
                    capability_count=manifest.capability_count,
                    updated_at=datetime.now(UTC),
                )
            )
            return manifest

    def _upsert_registry(self, entry: KnowledgeRegistryEntry) -> None:
        with self.lock("registry"):
            registry = self._read_registry_unlocked()
            entries = [item for item in registry.entries if item.key != entry.key]
            entries.append(entry)
            entries.sort(key=lambda item: item.key)
            updated = KnowledgeRegistry(
                schema_version=STORE_SCHEMA_VERSION,
                generated_at=datetime.now(UTC),
                entries=entries,
            )
            atomic_write_text(
                self.home / REGISTRY_FILENAME,
                _json_dump(updated.model_dump(mode="json")),
            )

    # ------------------------------------------------------------------- read
    def _read_registry_unlocked(self) -> KnowledgeRegistry:
        path = self.home / REGISTRY_FILENAME
        if not path.is_file():
            return KnowledgeRegistry(generated_at=datetime.now(UTC), entries=[])
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return KnowledgeRegistry.model_validate(data)
        except (OSError, ValueError, ValidationError):
            quarantined = self._quarantine_file(path, "registry")
            logger.warning(
                "knowledge registry was corrupt and has been quarantined",
                extra={"path": str(path), "quarantined": str(quarantined)},
            )
            return KnowledgeRegistry(generated_at=datetime.now(UTC), entries=[])

    def read_registry(self) -> KnowledgeRegistry:
        """Registry contents; a corrupt registry is quarantined, not fatal."""
        return self._read_registry_unlocked()

    def find(self, technology: str, version: str) -> KnowledgeRegistryEntry | None:
        normalized = version.strip()
        return self.read_registry().find(technology, normalized)

    def lookup(self, target: KnowledgeTarget) -> KnowledgeRegistryEntry | None:
        registry = self.read_registry()
        found = registry.by_key(self.key_for(target))
        if found is not None:
            return found
        return registry.find(
            target.technology,
            normalized_store_version(target.ecosystem, target.version),
        )

    def read_manifest(
        self, target: KnowledgeTarget, *, verify: bool = True
    ) -> KnowledgeManifest | None:
        path = self.manifest_path(target)
        if not path.is_file():
            return None
        try:
            manifest = KnowledgeManifest.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, ValidationError) as exc:
            raise StoreCorruptedError(
                f"cannot read knowledge manifest for {self.key_for(target)}: {exc}"
            ) from exc
        if verify:
            validation = self.validate_entry(target)
            if not validation.ok:
                raise StoreCorruptedError(
                    "knowledge entry failed hash verification: " + "; ".join(validation.issues)
                )
        return manifest

    def validate_entry(self, target: KnowledgeTarget) -> StoreValidation:
        entry_dir = self.entry_dir(target)
        if not entry_dir.is_dir():
            return StoreValidation(False, ["entry directory is missing"])
        try:
            manifest = self.read_manifest(target, verify=False)
        except StoreCorruptedError as exc:
            return StoreValidation(False, [str(exc)])
        if manifest is None:
            return StoreValidation(False, ["manifest.json is missing"])
        issues: list[str] = []
        registered = self.read_registry().by_key(self.key_for(target))
        if registered is None:
            issues.append("entry is not registered")
        elif sha256_file(self.manifest_path(target)) != registered.manifest_hash:
            issues.append("hash mismatch: manifest.json")
        for required in CONTENT_FILENAMES:
            if required not in manifest.source_hashes:
                issues.append(f"missing manifest hash: {required}")
        for filename, digest in manifest.source_hashes.items():
            try:
                path = safe_join(entry_dir, filename)
            except PathViolation:
                issues.append(f"unsafe file path: {filename}")
                continue
            if not path.is_file():
                issues.append(f"missing file: {filename}")
                continue
            try:
                actual = sha256_file(path)
            except OSError as exc:  # pragma: no cover - unreadable file
                issues.append(f"unreadable file: {filename} ({exc})")
                continue
            if actual != digest:
                issues.append(f"hash mismatch: {filename}")
        return StoreValidation(not issues, issues)

    def read_sources(
        self, target: KnowledgeTarget, *, verify: bool = True
    ) -> list[KnowledgeSource]:
        if verify:
            validation = self.validate_entry(target)
            if not validation.ok:
                raise StoreCorruptedError("; ".join(validation.issues))
        return self._read_json_list(target, SOURCES_FILENAME, KnowledgeSource)

    def read_capabilities(self, target: KnowledgeTarget) -> list[dict[str, Any]]:
        return self._read_jsonl(target, CAPABILITIES_FILENAME)

    def read_relationships(self, target: KnowledgeTarget) -> list[dict[str, Any]]:
        return self._read_jsonl(target, RELATIONSHIPS_FILENAME)

    def read_json_file(self, target: KnowledgeTarget, filename: str) -> Any:
        path = safe_join(self.entry_dir(target), filename)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise StoreCorruptedError(
                f"cannot read {filename} for {self.key_for(target)}: {exc}"
            ) from exc

    def read_entry(self, target: KnowledgeTarget) -> KnowledgeEntry | None:
        if not self.entry_dir(target).is_dir():
            return None
        validation = self.validate_entry(target)
        if not validation.ok:
            raise StoreCorruptedError("; ".join(validation.issues))
        manifest = self.read_manifest(target, verify=False)
        if manifest is None:
            return None
        return KnowledgeEntry(
            manifest=manifest,
            sources=self.read_sources(target, verify=False),
            capabilities=self.read_capabilities(target),
            relationships=self.read_relationships(target),
            domains=self.read_json_file(target, DOMAINS_FILENAME),
            coverage=self.read_json_file(target, COVERAGE_FILENAME),
            conflicts=self.read_json_file(target, CONFLICTS_FILENAME),
        )

    def is_reusable(self, target: KnowledgeTarget, *, store_hash: str | None = None) -> bool:
        """Cheap reuse check: entry present and (optionally) hash-matching."""
        entry = self.lookup(target)
        if entry is None:
            return False
        if store_hash is not None and entry.store_hash != store_hash:
            return False
        return self.manifest_path(target).is_file() and self.validate_entry(target).ok

    # -------------------------------------------------------------- recovery
    def quarantine_entry(self, target: KnowledgeTarget) -> Path | None:
        """Move a corrupt entry aside (``.corrupt-<ts>``) and unregister it."""
        entry_dir = self.entry_dir(target)
        if not entry_dir.exists():
            return None
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        candidate = entry_dir.parent / f"{entry_dir.name}.corrupt-{stamp}"
        counter = 1
        while candidate.exists():
            candidate = entry_dir.parent / f"{entry_dir.name}.corrupt-{stamp}-{counter}"
            counter += 1
        try:
            entry_dir.rename(candidate)
        except OSError:  # pragma: no cover - racing writer
            return None
        with self.lock(self.key_for(target)):
            registry = self._read_registry_unlocked()
            entries = [item for item in registry.entries if item.key != self.key_for(target)]
            if len(entries) != len(registry.entries):
                updated = KnowledgeRegistry(
                    schema_version=STORE_SCHEMA_VERSION,
                    generated_at=datetime.now(UTC),
                    entries=entries,
                )
                atomic_write_text(
                    self.home / REGISTRY_FILENAME,
                    _json_dump(updated.model_dump(mode="json")),
                )
        return candidate

    def _quarantine_file(self, path: Path, label: str) -> Path | None:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        candidate = path.with_name(f"{path.name}.corrupt-{stamp}")
        counter = 1
        while candidate.exists():
            candidate = path.with_name(f"{path.name}.corrupt-{stamp}-{counter}")
            counter += 1
        try:
            path.rename(candidate)
        except OSError:  # pragma: no cover - defensive
            return None
        logger.warning("quarantined corrupt knowledge file %s", label)
        return candidate

    # --------------------------------------------------------------- helpers
    def _read_json_list(
        self, target: KnowledgeTarget, filename: str, model: type[BaseModel]
    ) -> list:
        payload = self.read_json_file(target, filename)
        if not isinstance(payload, list):
            raise StoreCorruptedError(f"{filename} must contain a JSON list")
        try:
            return [model.model_validate(item) for item in payload]
        except ValidationError as exc:
            raise StoreCorruptedError(f"invalid records in {filename}: {exc}") from exc

    def _read_jsonl(self, target: KnowledgeTarget, filename: str) -> list[dict[str, Any]]:
        path = self.entry_dir(target) / filename
        if not path.is_file():
            raise StoreCorruptedError(f"missing file: {filename}")
        records: list[dict[str, Any]] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                item = json.loads(line)
                if not isinstance(item, dict):
                    raise StoreCorruptedError(f"{filename} contains a non-object line")
                records.append(item)
        except (OSError, ValueError) as exc:
            raise StoreCorruptedError(f"cannot read {filename}: {exc}") from exc
        return records


__all__ = [
    "CAPABILITIES_FILENAME",
    "COMPILED_DIRNAME",
    "CONTENT_FILENAMES",
    "DEFAULT_LOCK_TIMEOUT",
    "LOCK_STALE_SECONDS",
    "MANIFEST_FILENAME",
    "REGISTRY_FILENAME",
    "STORE_SCHEMA_VERSION",
    "KnowledgeEntry",
    "KnowledgeStore",
    "StoreCorruptedError",
    "StoreError",
    "StoreLockedError",
    "StoreValidation",
    "knowledge_home",
    "normalized_store_version",
    "technology_slug",
]
