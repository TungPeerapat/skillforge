"""Skill learning and verification engine (v0.2).

The engine plans controlled execution from repository evidence, executes it only
after explicit approval, records redacted attempts, diagnoses failures with
deterministic rules, retries within hard limits, and turns a verified run into
learned knowledge that upgrades the existing skill.
"""

from __future__ import annotations

from skillforge.learning.fingerprint import (
    StalenessReport,
    check_staleness,
    repository_fingerprint,
    sha256_file,
    workflow_fingerprint,
)
from skillforge.learning.generalize import (
    generalize,
    render_troubleshooting,
    render_verified_workflow,
    sanitize_text,
    verification_section,
)
from skillforge.learning.planning import PlanOptions, build_execution_plan
from skillforge.learning.session import DryRunSession, LearningSessionRunner, RetryApproval
from skillforge.learning.store import (
    list_session_files,
    load_session,
    new_session_id,
    save_session,
    session_path,
)
from skillforge.learning.targets import TARGETS, LearningTarget, get_target, target_ids

__all__ = [
    "TARGETS",
    "DryRunSession",
    "LearningSessionRunner",
    "LearningTarget",
    "PlanOptions",
    "RetryApproval",
    "StalenessReport",
    "build_execution_plan",
    "check_staleness",
    "generalize",
    "get_target",
    "list_session_files",
    "load_session",
    "new_session_id",
    "render_troubleshooting",
    "render_verified_workflow",
    "repository_fingerprint",
    "sanitize_text",
    "save_session",
    "session_path",
    "sha256_file",
    "target_ids",
    "verification_section",
    "workflow_fingerprint",
]
