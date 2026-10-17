"""Artifact introspection registry (K6).

``introspect_artifact`` dispatches to the ecosystem introspector; every
implementation is static (AST/text/zip only) and never executes dependency
code.
"""

from __future__ import annotations

from skillforge.knowledge.introspect.base import (
    ArtifactIntrospector,
    ArtifactLocation,
    Budgets,
    IntrospectionResult,
    confined_path,
    evidence_for,
    locate_artifact,
)
from skillforge.knowledge.introspect.dart_pkg import DartIntrospector
from skillforge.knowledge.introspect.dotnet_pkg import NuGetIntrospector
from skillforge.knowledge.introspect.go_pkg import GoIntrospector
from skillforge.knowledge.introspect.java_pkg import JavaIntrospector
from skillforge.knowledge.introspect.node_pkg import NodeIntrospector
from skillforge.knowledge.introspect.python_pkg import PythonIntrospector
from skillforge.models import Certainty

_INTROSPECTORS: dict[str, ArtifactIntrospector] = {
    introspector.ecosystem: introspector
    for introspector in (
        PythonIntrospector(),
        NodeIntrospector(),
        NuGetIntrospector(),
        GoIntrospector(),
        DartIntrospector(),
        JavaIntrospector(),
    )
}


def introspector_for(ecosystem: str) -> ArtifactIntrospector | None:
    return _INTROSPECTORS.get(ecosystem)


def introspect_artifact(
    location: ArtifactLocation,
    *,
    technology: str | None = None,
    version: str | None = None,
    budgets: Budgets | None = None,
) -> IntrospectionResult:
    """Introspect one artifact; unknown ecosystems report UNKNOWN honestly."""
    introspector = _INTROSPECTORS.get(location.ecosystem)
    if introspector is None:
        return IntrospectionResult(
            diagnostics=[f"no static introspector for ecosystem '{location.ecosystem}'"],
            accuracy=Certainty.UNKNOWN,
        )
    if not confined_path(location.directory.parent, location.directory):
        return IntrospectionResult(
            accuracy=Certainty.UNKNOWN, diagnostics=["unsafe artifact path refused"]
        )
    result = introspector.introspect(
        location,
        technology=technology or location.name,
        version=version or location.version,
        budgets=budgets or Budgets(),
    )
    if not result.drafts:
        result.accuracy = Certainty.UNKNOWN
        result.diagnostics.append("no supported public API evidence found; coverage is UNKNOWN")
    return result


__all__ = [
    "ArtifactIntrospector",
    "ArtifactLocation",
    "Budgets",
    "IntrospectionResult",
    "evidence_for",
    "introspect_artifact",
    "introspector_for",
    "locate_artifact",
]
