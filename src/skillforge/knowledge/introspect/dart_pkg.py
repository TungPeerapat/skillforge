"""Dart artifact introspection (K6): bounded source scan of ``lib/``.

v1 scope: public classes, enums, mixins, extensions, and top-level functions
(``_private`` names excluded). Deeper export-graph analysis is deferred.
"""

from __future__ import annotations

import re
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
from skillforge.utils.fs import read_text_capped

_CLASS_RE = re.compile(r"^(?:abstract\s+|sealed\s+|base\s+|final\s+)*class\s+(?P<name>\w+)")
_ENUM_RE = re.compile(r"^enum\s+(?P<name>\w+)")
_MIXIN_RE = re.compile(r"^mixin\s+(?P<name>\w+)")
_EXTENSION_RE = re.compile(r"^extension\s+(?P<name>\w+)")
_FUNCTION_RE = re.compile(
    r"^(?:[\w<>,?.\s]+\s+)?(?P<name>[a-z]\w*)\s*(?:<[^>]*>)?\s*\(", re.MULTILINE
)


class DartIntrospector:
    """Static reader for Dart package sources."""

    ecosystem = "pub"

    def introspect(
        self,
        location: ArtifactLocation,
        *,
        technology: str,
        version: str,
        budgets: Budgets,
    ) -> IntrospectionResult:
        result = IntrospectionResult(accuracy=Certainty.FACT)
        root = location.directory
        lib_dir = root / "lib"
        search_root = lib_dir if confined_path(root, lib_dir) and lib_dir.is_dir() else root
        files: list[Path] = []
        for path in artifact_files(search_root, ".dart", budgets):
            if "test" in path.relative_to(search_root).parts:
                continue
            if len(path.relative_to(search_root).parts) > 5:
                continue
            files.append(path)
            if len(files) >= budgets.max_files_per_artifact:
                result.diagnostics.append("file budget reached")
                break
        drafts: list[CapabilityDraft] = []
        for path in files:
            if len(drafts) >= budgets.max_symbols_per_artifact:
                result.diagnostics.append("symbol budget reached")
                break
            text, truncated = read_text_capped(path, budgets.max_file_bytes)
            relative = path.relative_to(root).as_posix()
            if truncated:
                result.diagnostics.append(f"truncated file: {relative}")
            package = root.name
            depth = 0
            for line_number, raw in enumerate(text.splitlines(), start=1):
                stripped = raw.strip()
                if stripped.startswith("//"):
                    continue
                at_top_level = depth == 0
                kind: CapabilityKind | None = None
                detail = ""
                name = ""
                match = _CLASS_RE.match(stripped)
                if match:
                    name, kind = match.group("name"), CapabilityKind.CLASS
                else:
                    match = _ENUM_RE.match(stripped)
                    if match:
                        name, kind = match.group("name"), CapabilityKind.ENUM
                    else:
                        match = _MIXIN_RE.match(stripped)
                        if match:
                            name, kind, detail = (
                                match.group("name"),
                                CapabilityKind.CLASS,
                                "mixin",
                            )
                        else:
                            match = _EXTENSION_RE.match(stripped)
                            if match:
                                name, kind, detail = (
                                    match.group("name"),
                                    CapabilityKind.TYPE,
                                    "extension",
                                )
                            elif at_top_level:
                                match = _FUNCTION_RE.match(stripped)
                                if match and not stripped.startswith(
                                    ("if", "for", "while", "return")
                                ):
                                    name, kind = match.group("name"), CapabilityKind.FUNCTION
                if name and kind is not None and not name.startswith("_"):
                    drafts.append(
                        CapabilityDraft(
                            technology=technology,
                            ecosystem=self.ecosystem,
                            version=version,
                            kind=kind,
                            kind_detail=detail,
                            name=name,
                            qualified_name=f"{package}.{name}",
                            confidence=Certainty.FACT,
                            evidence=[
                                evidence_for(
                                    kind="ast_export",
                                    relative=relative,
                                    locator=f"L{line_number}",
                                    detail="Dart public declaration (source scan)",
                                    file_path=path,
                                )
                            ],
                        )
                    )
                depth += stripped.count("{") - stripped.count("}")
                depth = max(0, depth)
        drafts.sort(key=lambda item: (item.qualified_name, item.kind.rank, item.name))
        result.drafts = [
            draft.model_copy(update={"confidence": Certainty.INFERENCE})
            for draft in drafts[: budgets.max_symbols_per_artifact]
        ]
        result.accuracy = Certainty.INFERENCE
        result.diagnostics.append(
            "Dart declaration names only; export graph and signatures are UNKNOWN"
        )
        result.stats = {"files_scanned": len(files), "symbols": len(drafts)}
        return result


__all__ = ["DartIntrospector"]
