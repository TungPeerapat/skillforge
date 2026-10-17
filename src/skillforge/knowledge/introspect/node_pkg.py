"""Node artifact introspection (K6): package.json + a custom ``.d.ts`` scanner.

Per §33 #6 this is a hand-written v1 declaration scanner — no tree-sitter.
It reads declarations, class/interface members, re-exports (``export *`` one
level, ``export {…}``), merges overloads, and never executes JavaScript.
Complex generics are captured textually and marked INFERENCE.
"""

from __future__ import annotations

import json
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
from skillforge.models import (
    CapabilityDraft,
    CapabilityKind,
    CapabilityParameter,
    CapabilitySignature,
    Certainty,
)
from skillforge.utils.fs import read_text_capped

_DECL_RE = re.compile(
    r"^\s*export\s+(?:declare\s+)?(?P<kw>namespace|module|class|interface|enum|type|function|const|let|var)\s+"
    r"(?P<name>[A-Za-z_$][\w$]*)"
)
_DECLARE_ONLY_RE = re.compile(
    r"^\s*declare\s+(?P<kw>namespace|module|class|interface|enum|type|function|const|let|var)\s+"
    r"(?P<name>[A-Za-z_$][\w$]*)"
)
_MEMBER_RE = re.compile(
    r"^\s{2,}(?:public\s+|private\s+|protected\s+|readonly\s+|static\s+|abstract\s+)*"
    r"(?P<name>[A-Za-z_$][\w$]*)\s*(?:<[^>]*>)?\s*\("
)
_PROPERTY_RE = re.compile(r"^\s{2,}(?:readonly\s+)?(?P<name>[A-Za-z_$][\w$]*)\??\s*:")
_EXPORT_LIST_RE = re.compile(r"^\s*export\s*\{\s*(?P<body>[^}]*)\}")
_EXPORT_STAR_RE = re.compile(r"^\s*export\s+\*\s+from\s+[\"'](?P<path>[^\"']+)[\"']")

_KIND_BY_KEYWORD = {
    "namespace": CapabilityKind.NAMESPACE,
    "module": CapabilityKind.NAMESPACE,
    "class": CapabilityKind.CLASS,
    "interface": CapabilityKind.INTERFACE,
    "enum": CapabilityKind.ENUM,
    "type": CapabilityKind.TYPE,
    "function": CapabilityKind.FUNCTION,
    "const": CapabilityKind.CONSTANT,
    "let": CapabilityKind.CONSTANT,
    "var": CapabilityKind.CONSTANT,
}

_SIGNATURE_KINDS = {CapabilityKind.FUNCTION, CapabilityKind.METHOD}


class NodeIntrospector:
    """Static reader for installed npm packages."""

    ecosystem = "npm"

    def introspect(
        self,
        location: ArtifactLocation,
        *,
        technology: str,
        version: str,
        budgets: Budgets,
    ) -> IntrospectionResult:
        result = IntrospectionResult(accuracy=Certainty.FACT)
        package_json = self._read_package_json(location, budgets, result)
        files = self._declaration_files(location.directory, package_json, budgets)
        drafts: list[CapabilityDraft] = []
        if not files:
            result.diagnostics.append(
                "no TypeScript declarations found; public API reported as UNKNOWN (native-only gap)"
            )
            result.accuracy = Certainty.UNKNOWN
        else:
            visited: set[Path] = set()
            queue: list[tuple[Path, int]] = [(path, 0) for path in files]
            scanned_files = 0
            while queue and scanned_files < budgets.max_files_per_artifact:
                path, depth = queue.pop(0)
                if path in visited:
                    continue
                visited.add(path)
                scanned_files += 1
                text, truncated = read_text_capped(path, budgets.max_file_bytes)
                relative = self._relative(location.directory, path)
                if truncated:
                    result.diagnostics.append(
                        f"truncated at {budgets.max_file_bytes} bytes: {relative}"
                    )
                    continue
                file_drafts, star_targets = self._scan_declarations(
                    text,
                    technology=technology,
                    version=version,
                    relative=relative,
                    file=path,
                )
                drafts.extend(file_drafts)
                if depth == 0:
                    for target in star_targets:
                        resolved = self._resolve_relative(path, target, location.directory)
                        if resolved is not None and resolved not in visited:
                            queue.append((resolved, depth + 1))
            result.stats["files_scanned"] = scanned_files
        drafts.extend(self._bin_drafts(location.directory, package_json, technology, version))
        drafts = self._merge_overloads(drafts)
        drafts.sort(key=lambda item: (item.qualified_name, item.kind.rank, item.name))
        if len(drafts) > budgets.max_symbols_per_artifact:
            result.diagnostics.append("symbol budget exceeded; extra drafts dropped")
            drafts = drafts[: budgets.max_symbols_per_artifact]
        result.drafts = drafts
        if files:
            result.accuracy = Certainty.INFERENCE
            result.diagnostics.append(
                "Custom declaration scanner; complex TypeScript semantics are UNKNOWN"
            )
        result.stats["symbols"] = len(drafts)
        return result

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _relative(root: Path, path: Path) -> str:
        try:
            return path.relative_to(root).as_posix()
        except ValueError:  # pragma: no cover - defensive
            return path.name

    def _read_package_json(
        self, location: ArtifactLocation, budgets: Budgets, result: IntrospectionResult
    ) -> dict[str, object]:
        path = location.directory / "package.json"
        if not confined_path(location.directory, path) or not path.is_file():
            result.diagnostics.append("package.json is missing")
            result.accuracy = Certainty.UNKNOWN
            return {}
        text, _ = read_text_capped(path, budgets.max_file_bytes)
        try:
            data = json.loads(text)
        except ValueError:
            result.diagnostics.append("package.json is not valid JSON")
            result.accuracy = Certainty.UNKNOWN
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _declaration_files(
        root: Path, package_json: dict[str, object], budgets: Budgets
    ) -> list[Path]:
        candidates: list[Path] = []

        def add(value: object) -> None:
            if isinstance(value, str):
                cleaned = value[2:] if value.startswith("./") else value
                candidates.append(root / cleaned)
            elif isinstance(value, dict):
                for item in value.values():
                    add(item)
            elif isinstance(value, list):
                for item in value:
                    add(item)

        for key in ("types", "typings"):
            add(package_json.get(key))
        add(package_json.get("exports"))
        existing = sorted(
            {
                path
                for path in candidates
                if path.name.endswith(".d.ts") and confined_path(root, path) and path.is_file()
            }
        )
        if existing:
            return existing[: budgets.max_files_per_artifact]
        return artifact_files(root, ".d.ts", budgets)

    @staticmethod
    def _resolve_relative(current: Path, spec: str, root: Path) -> Path | None:
        if not spec.startswith(("./", "../")):
            return None
        base = current.parent
        for suffix in ("", ".d.ts", "/index.d.ts"):
            candidate = base / f"{spec}{suffix}"
            if not confined_path(root, candidate):
                continue
            try:
                candidate.resolve().relative_to(root.resolve())
            except ValueError:
                return None
            if candidate.is_file():
                return candidate
        return None

    def _draft(
        self,
        *,
        technology: str,
        version: str,
        kind: CapabilityKind,
        name: str,
        qualified: str,
        relative: str,
        file: Path,
        parent: str | None = None,
        signature: CapabilitySignature | None = None,
        kind_detail: str = "",
        confidence: Certainty = Certainty.FACT,
        line: int = 0,
    ) -> CapabilityDraft:
        return CapabilityDraft(
            technology=technology,
            ecosystem=self.ecosystem,
            version=version,
            kind=kind,
            kind_detail=kind_detail,
            name=name,
            qualified_name=qualified,
            signature=signature,
            parent_qualified=parent,
            confidence=confidence,
            evidence=[
                evidence_for(
                    kind="declaration",
                    relative=relative,
                    locator=f"L{line}" if line else "",
                    detail=".d.ts declaration scan (no execution)",
                    file_path=file,
                )
            ],
        )

    def _scan_declarations(
        self,
        text: str,
        *,
        technology: str,
        version: str,
        relative: str,
        file: Path,
    ) -> tuple[list[CapabilityDraft], list[str]]:
        drafts: list[CapabilityDraft] = []
        star_targets: list[str] = []
        stack: list[tuple[str, str, CapabilityKind]] = []
        for line_number, raw in enumerate(text.splitlines(), start=1):
            stripped = raw.strip()
            if not stripped or stripped.startswith(("//", "/*", "*")):
                continue
            if stripped.startswith("}"):
                for _ in range(stripped.count("}")):
                    if stack:
                        stack.pop()
                continue
            parent = stack[-1][1] if stack else None
            parent_kind = stack[-1][2] if stack else None

            star = _EXPORT_STAR_RE.match(raw)
            if star:
                star_targets.append(star.group("path"))
                continue
            export_list = _EXPORT_LIST_RE.match(raw)
            if export_list:
                for piece in export_list.group("body").split(","):
                    piece = piece.strip()
                    if not piece or piece.startswith("type "):
                        piece = piece.removeprefix("type ").strip()
                    if not piece:
                        continue
                    name = piece.split(" as ")[-1].strip()
                    if not name:
                        continue
                    inferred = (
                        CapabilityKind.CLASS if name[:1].isupper() else CapabilityKind.FUNCTION
                    )
                    drafts.append(
                        self._draft(
                            technology=technology,
                            version=version,
                            kind=inferred,
                            name=name,
                            qualified=f"{parent}.{name}" if parent else name,
                            parent=parent,
                            relative=relative,
                            file=file,
                            kind_detail="re-export; kind inferred",
                            confidence=Certainty.INFERENCE,
                            line=line_number,
                        )
                    )
                continue

            declaration = _DECL_RE.match(raw) or _DECLARE_ONLY_RE.match(raw)
            if declaration:
                keyword = declaration.group("kw")
                name = declaration.group("name")
                kind = _KIND_BY_KEYWORD[keyword]
                qualified = f"{parent}.{name}" if parent else name
                signature = None
                confidence = Certainty.FACT
                if keyword == "function":
                    signature = self._function_signature(raw)
                    if signature is not None:
                        confidence = Certainty.INFERENCE
                drafts.append(
                    self._draft(
                        technology=technology,
                        version=version,
                        kind=kind,
                        name=name,
                        qualified=qualified,
                        parent=parent,
                        signature=signature,
                        relative=relative,
                        file=file,
                        kind_detail="namespace" if kind is CapabilityKind.NAMESPACE else "",
                        confidence=confidence,
                        line=line_number,
                    )
                )
                if raw.count("{") > raw.count("}"):
                    stack.append((name, qualified, kind))
                continue

            if parent_kind in (CapabilityKind.CLASS, CapabilityKind.INTERFACE):
                if re.match(r"\s*(private|protected)\b", raw):
                    continue
                member = _MEMBER_RE.match(raw)
                if member:
                    name = member.group("name")
                    drafts.append(
                        self._draft(
                            technology=technology,
                            version=version,
                            kind=CapabilityKind.METHOD,
                            name=name,
                            qualified=f"{parent}.{name}",
                            parent=parent,
                            relative=relative,
                            file=file,
                            kind_detail="constructor" if name == "constructor" else "",
                            confidence=Certainty.INFERENCE,
                            line=line_number,
                        )
                    )
                    continue
                if parent_kind is CapabilityKind.INTERFACE:
                    prop = _PROPERTY_RE.match(raw)
                    if prop:
                        name = prop.group("name")
                        drafts.append(
                            self._draft(
                                technology=technology,
                                version=version,
                                kind=CapabilityKind.PROPERTY,
                                name=name,
                                qualified=f"{parent}.{name}",
                                parent=parent,
                                relative=relative,
                                file=file,
                                confidence=Certainty.INFERENCE,
                                line=line_number,
                            )
                        )
        return drafts, star_targets

    @staticmethod
    def _function_signature(line: str) -> CapabilitySignature | None:
        open_index = line.find("(")
        close_index = line.rfind(")")
        if open_index < 0 or close_index <= open_index:
            return None
        parameters = _parse_parameters(line[open_index + 1 : close_index])
        returns = line[close_index + 1 :].strip().lstrip(":").rstrip(";").strip() or None
        return CapabilitySignature(
            parameters=parameters,
            returns=returns,
            kind="function",
        )

    def _bin_drafts(
        self,
        root: Path,
        package_json: dict[str, object],
        technology: str,
        version: str,
    ) -> list[CapabilityDraft]:
        bin_field = package_json.get("bin")
        names: list[str] = []
        if isinstance(bin_field, str):
            names = [technology]
        elif isinstance(bin_field, dict):
            names = [str(item) for item in bin_field]
        drafts: list[CapabilityDraft] = []
        for name in names:
            drafts.append(
                CapabilityDraft(
                    technology=technology,
                    ecosystem=self.ecosystem,
                    version=version,
                    kind=CapabilityKind.CLI_COMMAND,
                    kind_detail="bin entry",
                    name=name,
                    qualified_name=name,
                    confidence=Certainty.FACT,
                    evidence=[
                        evidence_for(
                            kind="package_exports",
                            relative="package.json",
                            locator=f"bin:{name}",
                            detail="package.json bin entry",
                            file_path=root / "package.json",
                        )
                    ],
                )
            )
        return drafts

    @staticmethod
    def _merge_overloads(drafts: list[CapabilityDraft]) -> list[CapabilityDraft]:
        merged: dict[tuple[str, str], CapabilityDraft] = {}
        counts: dict[tuple[str, str], int] = {}
        for draft in drafts:
            key = (draft.qualified_name, draft.kind.value)
            if key in merged:
                counts[key] = counts.get(key, 1) + 1
                continue
            merged[key] = draft
            counts[key] = 1
        result: list[CapabilityDraft] = []
        for key, draft in merged.items():
            if counts[key] > 1:
                detail = f"{counts[key]} overloads"
                existing = draft.kind_detail
                draft = draft.model_copy(
                    update={"kind_detail": f"{existing}, {detail}" if existing else detail}
                )
            result.append(draft)
        return result


def _split_top_level(text: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in text:
        if char in "([{<":
            depth += 1
        elif char in ")]}>":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    if current:
        parts.append("".join(current))
    return parts


def _parse_parameters(text: str) -> list[CapabilityParameter]:
    parameters: list[CapabilityParameter] = []
    for piece in _split_top_level(text):
        cleaned = piece.strip()
        if not cleaned:
            continue
        rest = cleaned.startswith("...")
        if rest:
            cleaned = cleaned[3:].strip()
        default: str | None = None
        if "=" in cleaned:
            cleaned, _, default_value = cleaned.partition("=")
            default = default_value.strip()
            cleaned = cleaned.strip()
        optional = "?" in cleaned.partition(":")[0]
        name, _, type_text = cleaned.partition(":")
        parameters.append(
            CapabilityParameter(
                name=name.strip().rstrip("?"),
                type=type_text.strip() or None,
                default=default,
                required=not (rest or optional or default is not None),
                kind="rest" if rest else "positional",
            )
        )
    return parameters


__all__ = ["NodeIntrospector"]
