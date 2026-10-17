"""Go artifact introspection (K6): bounded source scan of exported symbols.

Exported = capitalized identifier. Structs, interfaces, functions, methods,
constants, and variables are extracted with textual signatures (INFERENCE).
The optional ``go doc`` tool probe is not part of v1 (it would execute a
toolchain); source scanning is deterministic and offline.
"""

from __future__ import annotations

import re
from pathlib import Path

from skillforge.knowledge.introspect.base import (
    ArtifactLocation,
    Budgets,
    IntrospectionResult,
    artifact_files,
    evidence_for,
)
from skillforge.models import (
    CapabilityDraft,
    CapabilityKind,
    CapabilitySignature,
    Certainty,
)
from skillforge.utils.fs import read_text_capped

_FUNC_RE = re.compile(
    r"^func\s+(?:\((?P<recv>[^)]*)\)\s+)?(?P<name>\w+)\s*\((?P<sig>[^)]*)\)(?P<rest>.*)$"
)
_TYPE_RE = re.compile(r"^type\s+(?P<name>\w+)\s+(?P<rest>.+)$")
_VALUE_RE = re.compile(r"^(?:var|const)\s+(?P<name>\w+)(?:\s|$)")
_BLOCK_START_RE = re.compile(r"^(?:var|const|type)\s*\($")
_PACKAGE_RE = re.compile(r"^package\s+(?P<name>\w+)")


class GoIntrospector:
    """Static reader for Go module sources."""

    ecosystem = "go"

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
        files: list[Path] = []
        for path in artifact_files(root, ".go", budgets):
            if path.name.endswith("_test.go"):
                continue
            if "vendor" in path.parts or "testdata" in path.parts:
                continue
            if len(path.relative_to(root).parts) > 3:
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
            drafts.extend(
                self._file_drafts(
                    text,
                    technology=technology,
                    version=version,
                    relative=relative,
                    file=path,
                    budgets=budgets,
                )
            )
        drafts.sort(key=lambda item: (item.qualified_name, item.kind.rank, item.name))
        result.drafts = [
            draft.model_copy(update={"confidence": Certainty.INFERENCE})
            for draft in drafts[: budgets.max_symbols_per_artifact]
        ]
        result.accuracy = Certainty.INFERENCE
        result.diagnostics.append(
            "Go source heuristics only; signatures and visibility may be incomplete"
        )
        result.stats = {"files_scanned": len(files), "symbols": len(drafts)}
        return result

    def _file_drafts(
        self,
        text: str,
        *,
        technology: str,
        version: str,
        relative: str,
        file: Path,
        budgets: Budgets,
    ) -> list[CapabilityDraft]:
        drafts: list[CapabilityDraft] = []
        package = "pkg"
        in_block: str | None = None
        last_comment = ""
        for line_number, raw in enumerate(text.splitlines(), start=1):
            stripped = raw.strip()
            if not stripped:
                continue
            if stripped.startswith("//"):
                last_comment = stripped[2:].strip()
                continue
            package_match = _PACKAGE_RE.match(stripped)
            if package_match:
                package = package_match.group("name")
                continue
            if _BLOCK_START_RE.match(stripped):
                in_block = stripped.split()[0]
                continue
            if in_block is not None:
                if stripped == ")":
                    in_block = None
                    continue
                name = stripped.split("(", 1)[0].split()[0].rstrip(",") if stripped else ""
                if name and name[:1].isupper():
                    self._append_value(
                        drafts,
                        name=name,
                        package=package,
                        block=in_block,
                        rest=stripped,
                        technology=technology,
                        version=version,
                        relative=relative,
                        file=file,
                        line=line_number,
                        comment=last_comment,
                        budgets=budgets,
                    )
                continue
            if stripped.startswith("func "):
                match = _FUNC_RE.match(stripped)
                if match and match.group("name")[:1].isupper():
                    name = match.group("name")
                    receiver = match.group("recv") or ""
                    receiver_type = receiver.split()[-1].lstrip("*") if receiver else ""
                    parent = (
                        f"{package}.{receiver_type}"
                        if receiver_type and receiver_type[:1].isupper()
                        else None
                    )
                    kind = CapabilityKind.METHOD if receiver_type else CapabilityKind.FUNCTION
                    drafts.append(
                        CapabilityDraft(
                            technology=technology,
                            ecosystem=self.ecosystem,
                            version=version,
                            kind=kind,
                            name=name,
                            qualified_name=f"{parent}.{name}" if parent else f"{package}.{name}",
                            parent_qualified=parent,
                            signature=CapabilitySignature(kind=kind.value),
                            description=last_comment[:200],
                            confidence=Certainty.FACT,
                            evidence=[
                                evidence_for(
                                    kind="ast_export",
                                    relative=relative,
                                    locator=f"L{line_number}",
                                    detail="Go exported symbol (source scan)",
                                    file_path=file,
                                )
                            ],
                        )
                    )
                last_comment = ""
                continue
            type_match = _TYPE_RE.match(stripped)
            if type_match and type_match.group("name")[:1].isupper():
                name = type_match.group("name")
                rest = type_match.group("rest").strip()
                if rest.startswith("interface"):
                    kind, detail = CapabilityKind.INTERFACE, "interface"
                elif rest.startswith("struct"):
                    kind, detail = CapabilityKind.CLASS, "struct"
                else:
                    kind, detail = CapabilityKind.TYPE, "alias"
                drafts.append(
                    CapabilityDraft(
                        technology=technology,
                        ecosystem=self.ecosystem,
                        version=version,
                        kind=kind,
                        kind_detail=detail,
                        name=name,
                        qualified_name=f"{package}.{name}",
                        description=last_comment[:200],
                        confidence=Certainty.FACT,
                        evidence=[
                            evidence_for(
                                kind="ast_export",
                                relative=relative,
                                locator=f"L{line_number}",
                                detail="Go exported type (source scan)",
                                file_path=file,
                            )
                        ],
                    )
                )
                last_comment = ""
                continue
            value_match = _VALUE_RE.match(stripped)
            if value_match and value_match.group("name")[:1].isupper():
                name = value_match.group("name")
                drafts.append(
                    CapabilityDraft(
                        technology=technology,
                        ecosystem=self.ecosystem,
                        version=version,
                        kind=CapabilityKind.CONSTANT,
                        name=name,
                        qualified_name=f"{package}.{name}",
                        description=last_comment[:200],
                        confidence=Certainty.FACT,
                        evidence=[
                            evidence_for(
                                kind="ast_export",
                                relative=relative,
                                locator=f"L{line_number}",
                                detail="Go exported value (source scan)",
                                file_path=file,
                            )
                        ],
                    )
                )
            last_comment = ""
        return drafts

    def _append_value(
        self,
        drafts: list[CapabilityDraft],
        *,
        name: str,
        package: str,
        block: str,
        rest: str,
        technology: str,
        version: str,
        relative: str,
        file: Path,
        line: int,
        comment: str,
        budgets: Budgets,
    ) -> None:
        if len(drafts) >= budgets.max_symbols_per_artifact:
            return
        kind = CapabilityKind.TYPE if block == "type" else CapabilityKind.CONSTANT
        detail = ""
        if block == "type":
            detail = (
                "struct" if "struct" in rest else "interface" if "interface" in rest else "alias"
            )
            if detail == "struct":
                kind = CapabilityKind.CLASS
            elif detail == "interface":
                kind = CapabilityKind.INTERFACE
        drafts.append(
            CapabilityDraft(
                technology=technology,
                ecosystem=self.ecosystem,
                version=version,
                kind=kind,
                kind_detail=detail,
                name=name,
                qualified_name=f"{package}.{name}",
                description=comment[:200],
                confidence=Certainty.FACT,
                evidence=[
                    evidence_for(
                        kind="ast_export",
                        relative=relative,
                        locator=f"L{line}",
                        detail=f"Go exported {block} (source scan)",
                        file_path=file,
                    )
                ],
            )
        )


__all__ = ["GoIntrospector"]
