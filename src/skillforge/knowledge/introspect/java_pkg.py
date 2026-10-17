"""Java artifact introspection (K6): JAR central directory class names.

v1 scope per §33 #8: class names only (zip central directory), no class-file
constant-pool parsing. Inner classes (``$``) and ``module-info`` are excluded.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from skillforge.knowledge.introspect.base import (
    ArtifactLocation,
    Budgets,
    IntrospectionResult,
    artifact_files,
    confined_path,
    evidence_for,
)
from skillforge.models import CapabilityDraft, CapabilityKind, Certainty


class JavaIntrospector:
    """Static reader for JAR archives."""

    ecosystem = "maven"

    def introspect(
        self,
        location: ArtifactLocation,
        *,
        technology: str,
        version: str,
        budgets: Budgets,
    ) -> IntrospectionResult:
        result = IntrospectionResult(accuracy=Certainty.FACT)
        base = location.directory
        jars: list[Path]
        if base.is_file() and base.suffix == ".jar":
            jars = [base]
        else:
            jars = artifact_files(base, ".jar", budgets)
        if not jars:
            result.diagnostics.append("no JAR archives found; class listing is UNKNOWN")
            result.accuracy = Certainty.UNKNOWN
            return result
        drafts: list[CapabilityDraft] = []
        scanned = 0
        for jar in jars:
            if not confined_path(base if base.is_dir() else base.parent, jar):
                continue
            if jar.stat().st_size > budgets.max_file_bytes:
                result.diagnostics.append("JAR exceeds byte budget; central directory not read")
                continue
            scanned += 1
            try:
                with zipfile.ZipFile(jar) as archive:
                    names = archive.namelist()
            except (OSError, zipfile.BadZipFile):
                result.diagnostics.append(f"unreadable archive skipped: {jar.name}")
                continue
            for entry in sorted(names):
                if not entry.endswith(".class") or entry.endswith("/module-info.class"):
                    continue
                if entry.startswith("META-INF/") or "$" in entry:
                    continue
                qualified = entry[: -len(".class")].replace("/", ".")
                name = qualified.rsplit(".", 1)[-1]
                if not name:
                    continue
                drafts.append(
                    CapabilityDraft(
                        technology=technology,
                        ecosystem=self.ecosystem,
                        version=version,
                        kind=CapabilityKind.CLASS,
                        name=name,
                        qualified_name=qualified,
                        confidence=Certainty.INFERENCE,
                        evidence=[
                            evidence_for(
                                kind="jar_entry",
                                relative=jar.name,
                                locator=entry,
                                detail="JAR central directory entry",
                                file_path=jar,
                            )
                        ],
                    )
                )
                if len(drafts) >= budgets.max_symbols_per_artifact:
                    result.diagnostics.append("symbol budget reached")
                    break
            if len(drafts) >= budgets.max_symbols_per_artifact:
                break
        drafts.sort(key=lambda item: (item.qualified_name, item.kind.rank, item.name))
        result.drafts = drafts
        result.accuracy = Certainty.INFERENCE
        result.diagnostics.append(
            "JAR class names only; public visibility and member API are UNKNOWN"
        )
        result.stats = {"files_scanned": scanned, "symbols": len(drafts)}
        return result


__all__ = ["JavaIntrospector"]
