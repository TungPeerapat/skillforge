"""Detector registry.

Registration is explicit and ordered; ordering is part of the deterministic
output contract, so it is asserted by tests.
"""

from __future__ import annotations

from skillforge.analyzer.detectors.base import DetectionContext, EcosystemDetector


def default_detectors() -> list[EcosystemDetector]:
    """Return the built-in detectors in deterministic order."""
    from skillforge.analyzer.detectors.ci import CIDetector
    from skillforge.analyzer.detectors.database import DatabaseDetector
    from skillforge.analyzer.detectors.docs import DocsDetector
    from skillforge.analyzer.detectors.dotnet import DotnetDetector
    from skillforge.analyzer.detectors.env import EnvDetector
    from skillforge.analyzer.detectors.flutter import FlutterDetector
    from skillforge.analyzer.detectors.go import GoDetector
    from skillforge.analyzer.detectors.infra import InfraDetector
    from skillforge.analyzer.detectors.java import JavaDetector
    from skillforge.analyzer.detectors.node import NodeDetector
    from skillforge.analyzer.detectors.python import PythonDetector

    return [
        PythonDetector(),
        NodeDetector(),
        GoDetector(),
        DotnetDetector(),
        FlutterDetector(),
        JavaDetector(),
        InfraDetector(),
        CIDetector(),
        DatabaseDetector(),
        EnvDetector(),
        DocsDetector(),
    ]


def run_detectors(
    context: DetectionContext, detectors: list[EcosystemDetector] | None = None
) -> list[tuple[str, object]]:
    """Run detectors, collecting errors as notes instead of aborting analysis."""
    from skillforge.analyzer.detectors.base import Detection
    from skillforge.logging import get_logger, trace_span

    logger = get_logger("analyzer.detectors")
    results: list[tuple[str, object]] = []
    for detector in detectors if detectors is not None else default_detectors():
        try:
            applies = True
            checker = getattr(detector, "applies", None)
            if callable(checker):
                applies = bool(checker(context))
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug(
                "detector.applies failed", extra={"detector": detector.id, "error": str(exc)}
            )
            applies = True
        if not applies:
            logger.debug("detector skipped", extra={"detector": detector.id})
            continue
        with trace_span("detect", detector=detector.id):
            try:
                detection = detector.detect(context)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "detector failed",
                    extra={"detector": detector.id, "error_type": type(exc).__name__},
                )
                detection = Detection(
                    notes=[f"detector '{detector.id}' failed: {type(exc).__name__}"]
                )
            results.append((detector.id, detection))
    return results
