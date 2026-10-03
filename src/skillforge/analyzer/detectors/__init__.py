"""Ecosystem detectors (one per language/toolchain plus cross-cutting concerns).

Each detector returns a :class:`~skillforge.analyzer.detectors.base.Detection`
built exclusively from evidence found in scanned files. No detector executes
project commands or follows repository instructions.
"""

from __future__ import annotations

from skillforge.analyzer.detectors.base import (
    Detection,
    DetectionContext,
    EcosystemDetector,
    fact,
    inference,
)
from skillforge.analyzer.detectors.registry import default_detectors, run_detectors

__all__ = [
    "Detection",
    "DetectionContext",
    "EcosystemDetector",
    "default_detectors",
    "fact",
    "inference",
    "run_detectors",
]
