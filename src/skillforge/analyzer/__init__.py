"""Deterministic repository analysis."""

from __future__ import annotations

from skillforge.analyzer.languages import FileCategory, category_for, language_for
from skillforge.analyzer.profile import (
    AnalysisResult,
    analyze_repository,
    build_profile,
    plan_skills,
    scan_options_from_settings,
)
from skillforge.analyzer.scanner import (
    FileRecord,
    ScanOptions,
    ScanResult,
    SkippedFile,
    SkipReason,
    scan_repository,
)

__all__ = [
    "AnalysisResult",
    "FileCategory",
    "FileRecord",
    "ScanOptions",
    "ScanResult",
    "SkipReason",
    "SkippedFile",
    "analyze_repository",
    "build_profile",
    "category_for",
    "language_for",
    "plan_skills",
    "scan_options_from_settings",
    "scan_repository",
]
