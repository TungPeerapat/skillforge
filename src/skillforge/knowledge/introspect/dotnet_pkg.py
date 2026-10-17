"""NuGet artifact introspection (K6): ``.nuspec`` + XML documentation members.

XML doc member IDs (``T:``, ``M:``, ``P:``, ``F:``, ``E:``) are exact names
shipped with the package; kind mapping from the prefix is heuristic
(INFERENCE). Raw assembly metadata parsing is deferred per §33 #7.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from skillforge.knowledge.introspect.base import (
    ArtifactLocation,
    Budgets,
    IntrospectionResult,
    artifact_files,
    evidence_for,
)
from skillforge.models import CapabilityDraft, CapabilityKind, Certainty
from skillforge.utils.fs import read_text_capped

_MEMBER_KINDS: dict[str, tuple[CapabilityKind, str]] = {
    "T": (CapabilityKind.CLASS, "xml-doc type"),
    "M": (CapabilityKind.METHOD, "xml-doc method"),
    "P": (CapabilityKind.PROPERTY, "xml-doc property"),
    "F": (CapabilityKind.CONSTANT, "xml-doc field"),
    "E": (CapabilityKind.EVENT, "xml-doc event"),
}
_DESCRIPTION_MAX = 200


def _summary(element: ET.Element) -> str:
    summary = element.find("summary")
    if summary is None:
        return ""
    text = " ".join(part.strip() for part in summary.itertext() if part.strip())
    return re.sub(r"\s+", " ", text)[:_DESCRIPTION_MAX]


def _parse_xml(text: str) -> ET.Element:
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ET.ParseError("DTD/entity declarations are refused")
    root = ET.fromstring(text)
    for node in root.iter():
        node.tag = node.tag.rsplit("}", 1)[-1]
    return root


class NuGetIntrospector:
    """Static reader for extracted NuGet packages."""

    ecosystem = "nuget"

    def introspect(
        self,
        location: ArtifactLocation,
        *,
        technology: str,
        version: str,
        budgets: Budgets,
    ) -> IntrospectionResult:
        result = IntrospectionResult(accuracy=Certainty.INFERENCE)
        drafts = self._nuspec_drafts(location, technology, version, budgets)
        seen: set[str] = set()
        xml_files = self._xml_files(location.directory, budgets)
        drafts.extend(self._xml_drafts(xml_files, technology, version, location, budgets, seen))
        drafts.sort(key=lambda item: (item.qualified_name, item.kind.rank, item.name))
        result.drafts = drafts[: budgets.max_symbols_per_artifact]
        result.diagnostics.append("XML-doc surface only; undocumented assembly API is UNKNOWN")
        result.stats = {"files_scanned": len(xml_files), "symbols": len(drafts)}
        return result

    def _nuspec_drafts(
        self, location: ArtifactLocation, technology: str, version: str, budgets: Budgets
    ) -> list[CapabilityDraft]:
        candidates = artifact_files(location.directory, ".nuspec", budgets)
        if not candidates:
            return []
        path = candidates[0]
        text, _ = read_text_capped(path, budgets.max_file_bytes)
        try:
            root = _parse_xml(text)
        except ET.ParseError:
            return []
        metadata = root.find("metadata")
        if metadata is None:
            return []
        description = ""
        description_element = metadata.find("description")
        if description_element is not None and description_element.text:
            description = re.sub(r"\s+", " ", description_element.text.strip())[:_DESCRIPTION_MAX]
        title = technology
        title_element = metadata.find("title")
        if title_element is not None and title_element.text:
            title = title_element.text.strip()
        return [
            CapabilityDraft(
                technology=technology,
                ecosystem=self.ecosystem,
                version=version,
                kind=CapabilityKind.PACKAGE,
                kind_detail="nuspec",
                name=title,
                qualified_name=technology,
                description=description,
                confidence=Certainty.FACT,
                evidence=[
                    evidence_for(
                        kind="nuget_metadata",
                        relative=path.name,
                        locator="metadata",
                        detail=".nuspec package metadata",
                        file_path=path,
                    )
                ],
            )
        ]

    @staticmethod
    def _xml_files(root: Path, budgets: Budgets) -> list[Path]:
        return artifact_files(root, ".xml", budgets)

    def _xml_drafts(
        self,
        files: list[Path],
        technology: str,
        version: str,
        location: ArtifactLocation,
        budgets: Budgets,
        seen: set[str],
    ) -> list[CapabilityDraft]:
        drafts: list[CapabilityDraft] = []
        for path in files:
            if len(drafts) >= budgets.max_symbols_per_artifact:
                break
            text, _ = read_text_capped(path, budgets.max_file_bytes)
            try:
                root = _parse_xml(text)
            except ET.ParseError:
                continue
            relative = path.relative_to(location.directory).as_posix()
            for member in root.iter("member"):
                name = member.get("name", "")
                if ":" not in name or len(drafts) >= budgets.max_symbols_per_artifact:
                    continue
                prefix, _, full_name = name.partition(":")
                mapping = _MEMBER_KINDS.get(prefix.upper())
                if mapping is None or full_name in seen:
                    continue
                seen.add(full_name)
                kind, detail = mapping
                base_name = full_name.split("(", 1)[0]
                parts = base_name.split(".")
                member_name = parts[-1]
                if prefix.upper() == "M" and member_name in ("#ctor", ".ctor"):
                    member_name = "constructor"
                parent = ".".join(parts[:-1]) if len(parts) > 1 else None
                drafts.append(
                    CapabilityDraft(
                        technology=technology,
                        ecosystem=self.ecosystem,
                        version=version,
                        kind=kind,
                        kind_detail=detail,
                        name=member_name,
                        qualified_name=base_name,
                        parent_qualified=parent,
                        description=_summary(member),
                        confidence=Certainty.FACT,
                        evidence=[
                            evidence_for(
                                kind="xml_doc",
                                relative=relative,
                                locator=name,
                                detail="XML documentation member",
                                file_path=path,
                            )
                        ],
                    )
                )
        return drafts


__all__ = ["NuGetIntrospector"]
