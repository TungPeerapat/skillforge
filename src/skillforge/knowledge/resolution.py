"""Exact version resolution (K2).

Resolution ladder per entry (first hit wins, always recorded):

1. ``LOCKFILE`` — exact version from a parsed lockfile (``FACT``);
2. ``INSTALLED_ARTIFACT`` — version read from local package metadata
   (``FACT``; metadata only, never imported);
3. ``MANIFEST_PIN`` — exact ``==``/``x.y.z`` manifest pin (``FACT``);
4. ``MANIFEST_RANGE`` / ``NONE`` — recorded as unresolved with a reason.

Disagreements are never resolved quietly: both claims are kept in a
:class:`~skillforge.models.knowledge.KnowledgeConflict` and the chosen claim is
justified by the ladder. Transitive lockfile-only packages enter the inventory
with ``importance=TRANSITIVE`` but never become deep-learning knowledge targets.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from skillforge.analyzer.profile import analyze_repository, scan_options_from_settings
from skillforge.analyzer.scanner import ScanResult
from skillforge.config import Settings
from skillforge.knowledge.artifacts import collect_installed
from skillforge.knowledge.inventory import PACKAGE_ECOSYSTEMS, build_inventory, classify_role
from skillforge.knowledge.lockfiles import parse_scan_lockfiles
from skillforge.knowledge.versions import normalize_version, parser_for
from skillforge.models import (
    Certainty,
    DependencyInventory,
    DependencyRelationship,
    InventoryEntry,
    KnowledgeConflict,
    KnowledgePriority,
    KnowledgeTarget,
    RepositoryProfile,
    ResolutionMethod,
    VersionClaim,
    VersionInfo,
)
from skillforge.models.repository import DependencyScope

_METHOD_PRIORITY: dict[ResolutionMethod, int] = {
    ResolutionMethod.LOCKFILE: 0,
    ResolutionMethod.INSTALLED_ARTIFACT: 1,
    ResolutionMethod.MANIFEST_PIN: 2,
}


@dataclass
class ResolutionResult:
    """Resolved inventory plus conflicts, warnings, and statistics."""

    inventory: DependencyInventory
    conflicts: list[KnowledgeConflict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)


def _dedupe_claims(claims: list[VersionClaim], ecosystem: str) -> list[VersionClaim]:
    seen: set[tuple[str, str, str]] = set()
    result: list[VersionClaim] = []
    for claim in claims:
        normalized = normalize_version(ecosystem, claim.version)
        marker = (claim.method.value, normalized or claim.version, claim.source)
        if marker in seen:
            continue
        seen.add(marker)
        result.append(claim)
    return result


def _claim_sort_key(claim: VersionClaim) -> tuple[int, str, str]:
    return (_METHOD_PRIORITY[claim.method], claim.version.lower(), claim.source)


def _claim_groups(
    claims: list[VersionClaim], ecosystem: str
) -> list[tuple[str, list[VersionClaim]]]:
    """Group claims that describe the same version under ecosystem rules."""
    parser = parser_for(ecosystem)
    groups: list[tuple[str, list[VersionClaim]]] = []
    for claim in claims:
        for representative, bucket in groups:
            result = parser.compare(claim.version, representative)
            same = (
                result == 0
                if result is not None
                else claim.version.strip() == representative.strip()
            )
            if same:
                bucket.append(claim)
                break
        else:
            groups.append((claim.version, [claim]))
    return groups


def _append_note(notes: list[str], note: str) -> None:
    if note not in notes:
        notes.append(note)


def _mark_unresolved(entry: InventoryEntry) -> None:
    if entry.relationship is not DependencyRelationship.DIRECT:
        return
    if entry.ecosystem not in PACKAGE_ECOSYSTEMS or entry.version.is_resolved:
        return
    if entry.version.method is ResolutionMethod.MANIFEST_RANGE:
        _append_note(
            entry.version.notes,
            "version range in the manifest with no lockfile or installed version; "
            "stays unresolved (never guessed)",
        )
    else:
        _append_note(
            entry.version.notes,
            "no exact version evidence in manifests, lockfiles, or installed artifacts",
        )


def resolve_inventory(
    inventory: DependencyInventory,
    *,
    root: Path,
    scan: ScanResult | None = None,
    profile: RepositoryProfile | None = None,
    include_installed: bool = True,
    settings: Settings | None = None,
) -> ResolutionResult:
    """Apply the resolution ladder to every inventory entry."""
    resolved = inventory.model_copy(deep=True)
    entries = resolved.entries
    identity: dict[tuple[str, str], InventoryEntry] = {
        (entry.ecosystem, entry.name.lower()): entry for entry in entries
    }
    claims: dict[str, list[VersionClaim]] = defaultdict(list)
    warnings: list[str] = []

    for entry in entries:
        if entry.version.method is ResolutionMethod.MANIFEST_PIN and entry.version.is_resolved:
            source = entry.version.resolved_from or (
                entry.manifests[0] if entry.manifests else "manifest"
            )
            claims[entry.key].append(
                VersionClaim(
                    version=entry.version.resolved or "",
                    method=ResolutionMethod.MANIFEST_PIN,
                    source=source,
                    detail="exact manifest pin",
                )
            )

    # ------------------------------------------------------------- lockfiles
    lockfile_claims: dict[tuple[str, str], list[VersionClaim]] = defaultdict(list)
    lockfile_names: dict[tuple[str, str], str] = {}
    lockfile_paths: dict[tuple[str, str], set[str]] = defaultdict(set)
    if scan is not None:
        parsed, parse_warnings = parse_scan_lockfiles(scan)
        warnings.extend(parse_warnings)
        warnings.extend(warning for warning in scan.warnings if "ockfile" in warning)
        for lock in parsed:
            for fact in lock.entries:
                key = (fact.ecosystem, fact.name.lower())
                lockfile_claims[key].append(
                    VersionClaim(
                        version=fact.version,
                        method=ResolutionMethod.LOCKFILE,
                        source=lock.path,
                        detail=f"{lock.format} entry",
                    )
                )
                lockfile_names.setdefault(key, fact.name)
                lockfile_paths[key].add(lock.path)

    new_transitive: list[InventoryEntry] = []
    for key in sorted(lockfile_claims):
        eco, _lowered = key
        if eco not in PACKAGE_ECOSYSTEMS:
            continue
        version_claims = _dedupe_claims(lockfile_claims[key], eco)
        if not version_claims:
            continue
        existing_entry = identity.get(key)
        if existing_entry is not None:
            claims[existing_entry.key].extend(version_claims)
            continue
        display_name = lockfile_names[key]
        chosen = sorted(version_claims, key=_claim_sort_key)[0]
        transitive = InventoryEntry(
            id=InventoryEntry.make_id(eco, display_name),
            ecosystem=eco,
            name=display_name,
            relationship=DependencyRelationship.TRANSITIVE,
            role=classify_role(display_name, []),
            scope=DependencyScope.UNKNOWN,
            importance=KnowledgePriority.TRANSITIVE,
            version=VersionInfo(
                resolved=chosen.version,
                method=ResolutionMethod.LOCKFILE,
                resolved_from=chosen.source,
                certainty=Certainty.FACT,
            ),
            lockfiles=sorted(lockfile_paths[key]),
            notes=["lockfile-only entry (transitive candidate); the parent graph is built later"],
        )
        identity[key] = transitive
        new_transitive.append(transitive)

    entries.extend(new_transitive)

    # ------------------------------------------------------------- installed
    if include_installed:
        component_paths = tuple(
            component.path
            for component in (profile.components if profile is not None else [])
            if getattr(component, "path", "") and component.path != "."
        )
        installed = collect_installed(root, component_paths=component_paths)
        warnings.extend(installed.warnings)
        for entry in entries:
            artifact = installed.artifacts.get((entry.ecosystem, entry.name.lower()))
            if artifact is None:
                continue
            claims[entry.key].append(
                VersionClaim(
                    version=artifact.version,
                    method=ResolutionMethod.INSTALLED_ARTIFACT,
                    source=artifact.path,
                    detail="installed artifact metadata",
                )
            )

    # ------------------------------------------------- resolve, record conflicts
    conflicts: list[KnowledgeConflict] = []
    for entry in entries:
        entry_claims = _dedupe_claims(claims.get(entry.key, []), entry.ecosystem)
        if not entry_claims:
            if scan is not None:
                _mark_unresolved(entry)
            continue
        groups = _claim_groups(entry_claims, entry.ecosystem)
        chosen = sorted(entry_claims, key=_claim_sort_key)[0]
        if len(groups) > 1:
            versions = sorted({representative for representative, _ in groups})
            conflicts.append(
                KnowledgeConflict(
                    ecosystem=entry.ecosystem,
                    name=entry.name,
                    claims=sorted(entry_claims, key=_claim_sort_key),
                    chosen=chosen.version,
                    reason=(
                        f"sources disagree ({', '.join(versions)}); "
                        f"chose {chosen.method.value} from {chosen.source} "
                        "per the resolution ladder"
                    ),
                )
            )
        entry.version.resolved = chosen.version
        entry.version.method = chosen.method
        entry.version.resolved_from = chosen.source
        entry.version.certainty = Certainty.FACT
        if chosen.method is ResolutionMethod.LOCKFILE and chosen.source not in entry.lockfiles:
            entry.lockfiles.append(chosen.source)
            entry.lockfiles.sort()

    conflicts.sort(key=lambda conflict: (conflict.ecosystem, conflict.name.lower()))

    # ------------------------------------------------------ sort, cap, targets
    limit = settings.knowledge.max_inventory_entries if settings is not None else 2_000
    ordered = sorted(
        resolved.entries,
        key=lambda item: (
            item.importance.rank,
            item.ecosystem,
            item.name.lower(),
            item.component or "",
        ),
    )
    truncated = resolved.truncated
    if len(ordered) > limit:
        ordered = ordered[:limit]
        truncated = True
        _append_note(
            resolved.warnings,
            f"resolved inventory truncated at {limit} entries (most important kept)",
        )

    targets: dict[str, KnowledgeTarget] = {}
    for entry in ordered:
        if entry.relationship is not DependencyRelationship.DIRECT:
            continue
        if entry.ecosystem not in PACKAGE_ECOSYSTEMS or not entry.version.is_resolved:
            continue
        target = KnowledgeTarget(
            ecosystem=entry.ecosystem,
            technology=entry.name,
            version=entry.version.resolved or "",
        )
        targets.setdefault(target.key, target)

    resolved.entries = ordered
    resolved.knowledge_targets = sorted(
        targets.values(), key=lambda item: (item.ecosystem, item.technology.lower())
    )
    resolved.warnings.extend(warnings)
    resolved.truncated = truncated

    direct_packages = [
        entry
        for entry in ordered
        if entry.relationship is DependencyRelationship.DIRECT
        and entry.ecosystem in PACKAGE_ECOSYSTEMS
    ]
    stats: dict[str, int] = {
        "entries": len(ordered),
        "transitive_entries": sum(
            1 for entry in ordered if entry.relationship is DependencyRelationship.TRANSITIVE
        ),
        "direct_packages": len(direct_packages),
        "resolved": sum(1 for entry in direct_packages if entry.version.is_resolved),
        "unresolved": sum(1 for entry in direct_packages if not entry.version.is_resolved),
        "conflicts": len(conflicts),
    }
    for method in (
        ResolutionMethod.LOCKFILE,
        ResolutionMethod.INSTALLED_ARTIFACT,
        ResolutionMethod.MANIFEST_PIN,
    ):
        stats[f"resolved_{method.value}"] = sum(
            1 for entry in direct_packages if entry.version.method is method
        )

    return ResolutionResult(inventory=resolved, conflicts=conflicts, warnings=warnings, stats=stats)


def resolve_repository(
    root: Path,
    settings: Settings,
    *,
    include_installed: bool = True,
) -> ResolutionResult:
    """Analyze-with-lockfiles → inventory → resolved inventory, in one call."""
    options = scan_options_from_settings(settings)
    options.read_lockfiles = settings.knowledge.read_lockfiles
    options.max_lockfile_size = settings.knowledge.max_lockfile_bytes
    resolved_root = Path(root).resolve()
    analysis = analyze_repository(resolved_root, settings, scan_options=options)
    inventory = build_inventory(analysis.profile, settings)
    return resolve_inventory(
        inventory,
        root=resolved_root,
        scan=analysis.scan,
        profile=analysis.profile,
        include_installed=include_installed,
        settings=settings,
    )


__all__ = [
    "ResolutionResult",
    "resolve_inventory",
    "resolve_repository",
]
