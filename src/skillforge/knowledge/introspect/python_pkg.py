"""Python artifact introspection (K6): AST over ``.py``/``.pyi``, never imports.

Prefers stubs: when a package ships ``.pyi`` files, only those are parsed.
Extracts classes, enums, exceptions, functions, methods, properties,
constants, type aliases, ``__all__`` re-exports (kind inferred → INFERENCE),
and console-script entry points.
"""

from __future__ import annotations

import ast
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

_MAX_RAISES = 10
_DESCRIPTION_MAX = 200


def _unparse(node: ast.expr | None) -> str | None:
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover - defensive
        return None


def _doc_first_line(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Module,
) -> str:
    raw = ast.get_docstring(node) or ""
    first = raw.strip().splitlines()[0].strip() if raw.strip() else ""
    return first[:_DESCRIPTION_MAX]


def _decorator_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    return ""


def _base_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _export_names(tree: ast.Module) -> set[str] | None:
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, (ast.List, ast.Tuple))
            and any(
                isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets
            )
        ):
            return {
                element.value
                for element in node.value.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            }
    return None


def _visible(name: str, exports: set[str] | None) -> bool:
    if exports is not None:
        return name in exports
    return not name.startswith("_")


def _assigned_names(node: ast.Assign | ast.AnnAssign) -> list[str]:
    if isinstance(node, ast.AnnAssign):
        return [node.target.id] if isinstance(node.target, ast.Name) else []
    names: list[str] = []
    for target in node.targets:
        if isinstance(target, ast.Name):
            names.append(target.id)
    return names


def _class_kind(node: ast.ClassDef) -> CapabilityKind:
    for base in node.bases:
        name = _base_name(base).lower()
        if name.endswith("enum"):
            return CapabilityKind.ENUM
        if name.endswith(("exception", "error", "baseexception")):
            return CapabilityKind.EXCEPTION
    return CapabilityKind.CLASS


def _raises(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    found: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Raise) or child.exc is None:
            continue
        exc = child.exc
        if isinstance(exc, ast.Call):
            exc = exc.func
        name = _base_name(exc) if isinstance(exc, (ast.Name, ast.Attribute)) else ""
        if name:
            found.add(name)
    return sorted(found)[:_MAX_RAISES]


def _build_signature(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    is_method: bool,
    static: bool,
) -> CapabilitySignature:
    args = node.args
    positional = [*args.posonlyargs, *args.args]
    defaults: list[ast.expr | None] = [None] * (len(positional) - len(args.defaults))
    defaults.extend(args.defaults)
    parameters: list[CapabilityParameter] = []
    for index, (arg, default) in enumerate(zip(positional, defaults, strict=True)):
        if is_method and not static and index == 0 and arg.arg in ("self", "cls"):
            continue
        parameters.append(
            CapabilityParameter(
                name=arg.arg,
                type=_unparse(arg.annotation),
                default=_unparse(default),
                required=default is None,
                kind="positional",
            )
        )
    if args.vararg is not None:
        parameters.append(
            CapabilityParameter(
                name=args.vararg.arg,
                type=_unparse(args.vararg.annotation),
                required=False,
                kind="var_positional",
            )
        )
    for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True):
        parameters.append(
            CapabilityParameter(
                name=arg.arg,
                type=_unparse(arg.annotation),
                default=_unparse(default),
                required=default is None,
                kind="keyword_only",
            )
        )
    if args.kwarg is not None:
        parameters.append(
            CapabilityParameter(
                name=args.kwarg.arg,
                type=_unparse(args.kwarg.annotation),
                required=False,
                kind="var_keyword",
            )
        )
    return CapabilitySignature(
        parameters=parameters,
        returns=_unparse(node.returns),
        raises=_raises(node),
        async_=isinstance(node, ast.AsyncFunctionDef),
        static=static,
        kind="method" if is_method else "function",
    )


class PythonIntrospector:
    """Static AST reader for installed Python packages."""

    ecosystem = "pypi"

    def introspect(
        self,
        location: ArtifactLocation,
        *,
        technology: str,
        version: str,
        budgets: Budgets,
    ) -> IntrospectionResult:
        result = IntrospectionResult(accuracy=Certainty.FACT)
        files = self._python_files(location.directory, budgets)
        if len(files) > budgets.max_files_per_artifact:
            result.diagnostics.append(
                f"file budget exceeded: parsed {budgets.max_files_per_artifact} of {len(files)}"
            )
            files = files[: budgets.max_files_per_artifact]
        drafts: list[CapabilityDraft] = []
        for file in files:
            if len(drafts) >= budgets.max_symbols_per_artifact:
                result.diagnostics.append("symbol budget reached; remaining files skipped")
                break
            text, truncated = read_text_capped(file, budgets.max_file_bytes)
            relative = self._relative(location, file)
            if truncated:
                result.diagnostics.append(
                    f"truncated at {budgets.max_file_bytes} bytes: {relative}"
                )
                continue
            try:
                tree = ast.parse(text)
            except (SyntaxError, ValueError):
                result.diagnostics.append(f"unparsable file skipped: {relative}")
                continue
            module = self._module_qualified(location.directory, file)
            drafts.extend(
                self._module_drafts(
                    tree,
                    technology=technology,
                    version=version,
                    module=module,
                    relative=relative,
                    file=file,
                )
            )
        drafts.extend(self._entry_point_drafts(location, technology, version))
        drafts.sort(key=lambda item: (item.qualified_name, item.kind.rank, item.name))
        if len(drafts) > budgets.max_symbols_per_artifact:
            result.diagnostics.append("symbol budget exceeded; extra drafts dropped")
            drafts = drafts[: budgets.max_symbols_per_artifact]
        result.drafts = drafts
        if any(draft.confidence is Certainty.INFERENCE for draft in drafts):
            result.accuracy = Certainty.INFERENCE
        result.stats = {"files_scanned": len(files), "symbols": len(drafts)}
        return result

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _relative(location: ArtifactLocation, file: Path) -> str:
        try:
            return file.relative_to(location.directory.parent).as_posix()
        except ValueError:  # pragma: no cover - defensive
            return file.name

    @staticmethod
    def _module_qualified(root: Path, file: Path) -> str:
        package = root.name
        relative = file.relative_to(root).with_suffix("")
        parts = list(relative.parts)
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        return ".".join([package, *parts]) if parts else package

    def _python_files(self, root: Path, budgets: Budgets) -> list[Path]:
        stubs = artifact_files(root, ".pyi", budgets)
        sources = artifact_files(root, ".py", budgets)
        # Prefer the stub for each module, not for the entire package: partial
        # stub packages must not hide modules that only ship Python sources.
        by_module = {path.with_suffix(""): path for path in sources}
        by_module.update({path.with_suffix(""): path for path in stubs})
        return sorted(by_module.values())[: budgets.max_files_per_artifact]

    def _draft(
        self,
        *,
        technology: str,
        version: str,
        kind: CapabilityKind,
        name: str,
        qualified: str,
        parent: str | None = None,
        signature: CapabilitySignature | None = None,
        description: str = "",
        kind_detail: str = "",
        confidence: Certainty = Certainty.FACT,
        relative: str = "",
        file: Path | None = None,
        lineno: int = 0,
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
            description=description,
            parent_qualified=parent,
            confidence=confidence,
            evidence=[
                evidence_for(
                    kind="ast_export",
                    relative=relative,
                    locator=f"L{lineno}" if lineno else "",
                    detail="AST/declaration scan (no execution)",
                    file_path=file,
                )
            ],
        )

    def _module_drafts(
        self,
        tree: ast.Module,
        *,
        technology: str,
        version: str,
        module: str,
        relative: str,
        file: Path,
    ) -> list[CapabilityDraft]:
        drafts: list[CapabilityDraft] = []
        exports = _export_names(tree)

        def base_draft(**kwargs: object) -> CapabilityDraft:
            return self._draft(
                technology=technology,
                version=version,
                relative=relative,
                file=file,
                **kwargs,  # type: ignore[arg-type]
            )

        reexport_kinds: list[tuple[str, CapabilityKind]] = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not _visible(node.name, exports):
                    continue
                decorators = {_decorator_name(item) for item in node.decorator_list}
                drafts.append(
                    base_draft(
                        kind=CapabilityKind.FUNCTION,
                        name=node.name,
                        qualified=f"{module}.{node.name}",
                        signature=_build_signature(
                            node, is_method=False, static="staticmethod" in decorators
                        ),
                        description=_doc_first_line(node),
                        kind_detail="async" if isinstance(node, ast.AsyncFunctionDef) else "",
                        lineno=node.lineno,
                    )
                )
            elif isinstance(node, ast.ClassDef):
                if not _visible(node.name, exports):
                    continue
                class_qualified = f"{module}.{node.name}"
                drafts.append(
                    base_draft(
                        kind=_class_kind(node),
                        name=node.name,
                        qualified=class_qualified,
                        description=_doc_first_line(node),
                        lineno=node.lineno,
                    )
                )
                drafts.extend(
                    self._class_members(
                        node,
                        technology=technology,
                        version=version,
                        class_qualified=class_qualified,
                        relative=relative,
                        file=file,
                    )
                )
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                for name in _assigned_names(node):
                    if not _visible(name, exports):
                        continue
                    if name.isupper():
                        drafts.append(
                            base_draft(
                                kind=CapabilityKind.CONSTANT,
                                name=name,
                                qualified=f"{module}.{name}",
                                lineno=node.lineno,
                            )
                        )
                    elif _unparse(getattr(node, "annotation", None)) in (
                        "TypeAlias",
                        "typing.TypeAlias",
                        "typing_extensions.TypeAlias",
                    ):
                        drafts.append(
                            base_draft(
                                kind=CapabilityKind.TYPE,
                                name=name,
                                qualified=f"{module}.{name}",
                                lineno=node.lineno,
                            )
                        )
            elif isinstance(node, ast.ImportFrom) and exports:
                for alias in node.names:
                    if alias.asname is None and alias.name in exports:
                        inferred = (
                            CapabilityKind.CLASS
                            if alias.name[:1].isupper()
                            else CapabilityKind.FUNCTION
                        )
                        reexport_kinds.append((alias.name, inferred))
        for name, inferred in reexport_kinds:
            drafts.append(
                base_draft(
                    kind=inferred,
                    name=name,
                    qualified=f"{module}.{name}",
                    kind_detail="re-export; kind inferred",
                    confidence=Certainty.INFERENCE,
                )
            )
        return drafts

    def _class_members(
        self,
        node: ast.ClassDef,
        *,
        technology: str,
        version: str,
        class_qualified: str,
        relative: str,
        file: Path,
    ) -> list[CapabilityDraft]:
        drafts: list[CapabilityDraft] = []
        for member in node.body:
            if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if member.name.startswith("_") and member.name != "__init__":
                    continue
                decorators = {_decorator_name(item) for item in member.decorator_list}
                if "property" in decorators:
                    drafts.append(
                        self._draft(
                            technology=technology,
                            version=version,
                            kind=CapabilityKind.PROPERTY,
                            name=member.name,
                            qualified=f"{class_qualified}.{member.name}",
                            parent=class_qualified,
                            description=_doc_first_line(member),
                            relative=relative,
                            file=file,
                            lineno=member.lineno,
                        )
                    )
                    continue
                details = []
                if "staticmethod" in decorators:
                    details.append("static")
                if "classmethod" in decorators:
                    details.append("classmethod")
                if isinstance(member, ast.AsyncFunctionDef):
                    details.append("async")
                drafts.append(
                    self._draft(
                        technology=technology,
                        version=version,
                        kind=CapabilityKind.METHOD,
                        name=member.name,
                        qualified=f"{class_qualified}.{member.name}",
                        parent=class_qualified,
                        signature=_build_signature(
                            member,
                            is_method=True,
                            static="staticmethod" in decorators,
                        ),
                        description=_doc_first_line(member),
                        kind_detail=", ".join(details),
                        relative=relative,
                        file=file,
                        lineno=member.lineno,
                    )
                )
            elif isinstance(member, ast.ClassDef):
                if not member.name.startswith("_"):
                    drafts.append(
                        self._draft(
                            technology=technology,
                            version=version,
                            kind=CapabilityKind.CLASS,
                            name=member.name,
                            qualified=f"{class_qualified}.{member.name}",
                            parent=class_qualified,
                            description=_doc_first_line(member),
                            relative=relative,
                            file=file,
                            lineno=member.lineno,
                        )
                    )
            elif isinstance(member, (ast.Assign, ast.AnnAssign)):
                for name in _assigned_names(member):
                    if name.isupper() and not name.startswith("_"):
                        drafts.append(
                            self._draft(
                                technology=technology,
                                version=version,
                                kind=CapabilityKind.CONSTANT,
                                name=name,
                                qualified=f"{class_qualified}.{name}",
                                parent=class_qualified,
                                relative=relative,
                                file=file,
                                lineno=member.lineno,
                            )
                        )
        return drafts

    def _entry_point_drafts(
        self, location: ArtifactLocation, technology: str, version: str
    ) -> list[CapabilityDraft]:
        if location.metadata_dir is None:
            return []
        path = location.metadata_dir / "entry_points.txt"
        if not confined_path(location.metadata_dir, path) or not path.is_file():
            return []
        text, _ = read_text_capped(path, 200_000)
        drafts: list[CapabilityDraft] = []
        section = ""
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("[") and stripped.endswith("]"):
                section = stripped.strip("[]").lower()
                continue
            if section != "console_scripts" or "=" not in stripped:
                continue
            name, _, target = stripped.partition("=")
            name = name.strip()
            if not name:
                continue
            drafts.append(
                CapabilityDraft(
                    technology=technology,
                    ecosystem=self.ecosystem,
                    version=version,
                    kind=CapabilityKind.CLI_COMMAND,
                    kind_detail="console script",
                    name=name,
                    qualified_name=name,
                    description=target.strip()[:200],
                    confidence=Certainty.FACT,
                    evidence=[
                        evidence_for(
                            kind="package_exports",
                            relative="entry_points.txt",
                            locator=name,
                            detail="console_scripts entry point",
                            file_path=path,
                        )
                    ],
                )
            )
        return drafts


__all__ = ["PythonIntrospector"]
