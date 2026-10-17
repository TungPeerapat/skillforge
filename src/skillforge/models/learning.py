"""Learning, execution, and verification domain models (pure data, no I/O).

These models back the ``skillforge learn`` workflow. They are deliberately
separate from the discovery models: everything here describes *controlled
execution* and *verified knowledge*, and none of it is produced without explicit
user approval.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from skillforge.models.common import Evidence, RiskLevel

#: Maximum number of attempts (including the first) per execution step.
MAX_STEP_ATTEMPTS = 5
#: Maximum number of attempts across one learning session.
MAX_SESSION_ATTEMPTS = 30
#: Default output cap kept per stream for an executed command.
DEFAULT_OUTPUT_LIMIT_BYTES = 64 * 1024


# --------------------------------------------------------------- verification


class VerificationStatus(StrEnum):
    """Lifecycle of a generated skill.

    ``STALE`` is derived at read time (the repository changed since
    verification) and is never persisted as a claim.
    """

    UNVERIFIED = "unverified"
    LEARNING = "learning"
    VERIFIED = "verified"
    FAILED = "failed"
    STALE = "stale"

    @property
    def label(self) -> str:
        return self.value.upper()


class RepositoryFingerprint(BaseModel):
    """Enough repository state to detect that a verified skill became stale."""

    model_config = ConfigDict(extra="forbid")

    git_commit: str | None = None
    git_dirty: bool | None = None
    file_hashes: dict[str, str] = Field(default_factory=dict)
    workflow_fingerprint: str = ""
    captured_at: datetime | None = None


class SkillVerification(BaseModel):
    """Verification metadata stored in ``.skillforge.json`` (never frontmatter)."""

    model_config = ConfigDict(extra="forbid")

    status: VerificationStatus = VerificationStatus.UNVERIFIED
    method: str = "static-analysis"
    session_id: str | None = None
    verified_at: datetime | None = None
    fingerprint: RepositoryFingerprint | None = None
    summary: str = ""

    @property
    def is_verified(self) -> bool:
        return self.status is VerificationStatus.VERIFIED

    @property
    def is_stale(self) -> bool:
        return self.status is VerificationStatus.STALE


# ------------------------------------------------------------------ execution


class ExecutionPhase(StrEnum):
    SETUP = "setup"
    SERVICES = "services"
    MIGRATION = "migration"
    LAUNCH = "launch"
    VERIFICATION = "verification"
    CLEANUP = "cleanup"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").title()


class StepAction(StrEnum):
    """What an execution step does."""

    COMMAND = "command"
    HTTP_GET = "http_get"
    WAIT_PORT = "wait_port"
    WAIT_LOG = "wait_log"
    WAIT_PROCESS = "wait_process"
    TERMINATE = "terminate"


class ReadinessKind(StrEnum):
    HTTP = "http"
    TCP = "tcp"
    LOG_SIGNAL = "log_signal"
    PROCESS = "process"


class ReadinessConfidence(StrEnum):
    """How strong the evidence behind a readiness check is."""

    #: The endpoint/port was directly observed in repository files.
    EVIDENCED = "evidenced"
    #: Derived from command flags, service mappings, or framework defaults.
    INFERRED = "inferred"
    #: Only "the process is still alive" could be checked.
    PROCESS_ONLY = "process_only"


class ReadinessPlan(BaseModel):
    """A deterministic strategy for deciding whether the app is up."""

    model_config = ConfigDict(extra="forbid")

    kind: ReadinessKind
    confidence: ReadinessConfidence = ReadinessConfidence.INFERRED
    url: str | None = None
    host: str | None = None
    port: int | None = None
    path: str | None = None
    expect_status: str = "2xx"
    log_pattern: str | None = None
    grace_seconds: float = 2.0
    evidence: list[Evidence] = Field(default_factory=list)
    detail: str = ""

    @property
    def target(self) -> str:
        if self.url:
            return self.url
        if self.host and self.port:
            return f"{self.host}:{self.port}"
        return self.kind.value


class ExecutionStep(BaseModel):
    """One step of an execution plan. Commands are always evidence-backed."""

    model_config = ConfigDict(extra="forbid")

    id: str
    phase: ExecutionPhase
    purpose: str
    action: StepAction = StepAction.COMMAND
    command: str = ""
    argv: list[str] = Field(default_factory=list)
    cwd: str = "."
    evidence: list[Evidence] = Field(default_factory=list)
    risk: RiskLevel = RiskLevel.REVIEW
    risk_reasons: list[str] = Field(default_factory=list)
    timeout_seconds: float = Field(default=120.0, gt=0, le=3600)
    expected_outcome: str = ""
    depends_on: list[str] = Field(default_factory=list)
    long_running: bool = False
    readiness: ReadinessPlan | None = None
    notes: list[str] = Field(default_factory=list)
    #: True for commands that were found but must never execute (DANGEROUS).
    excluded: bool = False

    @property
    def provenance(self) -> str:
        return self.evidence[0].label if self.evidence else "unknown"

    @property
    def executable(self) -> bool:
        return not self.excluded and self.action is StepAction.COMMAND and bool(self.argv)


class ExecutionPlan(BaseModel):
    """Result of planning a controlled execution, before any approval."""

    model_config = ConfigDict(extra="forbid")

    id: str
    target: str
    repository: str
    root_path: str
    created_at: datetime
    steps: list[ExecutionStep] = Field(default_factory=list)
    risk_summary: dict[str, int] = Field(default_factory=dict)
    excluded_dangerous: list[str] = Field(default_factory=list)
    fingerprint: RepositoryFingerprint | None = None
    notes: list[str] = Field(default_factory=list)

    @property
    def executable_steps(self) -> list[ExecutionStep]:
        return [step for step in self.steps if step.executable]

    @property
    def verified_steps(self) -> list[ExecutionStep]:
        return [
            step for step in self.steps if step.action is StepAction.COMMAND and not step.excluded
        ]

    def step(self, step_id: str) -> ExecutionStep | None:
        for step in self.steps:
            if step.id == step_id:
                return step
        return None


class AttemptStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    ABORTED = "aborted"

    @property
    def is_success(self) -> bool:
        return self is AttemptStatus.SUCCEEDED


class DiagnosisCategory(StrEnum):
    CONNECTION_REFUSED = "connection_refused"
    PORT_IN_USE = "port_in_use"
    MISSING_EXECUTABLE = "missing_executable"
    MISSING_MODULE = "missing_module"
    MISSING_ENV_VAR = "missing_env_var"
    TIMEOUT = "timeout"
    PERMISSION_DENIED = "permission_denied"
    NONZERO_EXIT = "nonzero_exit"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ")


class ProposedActionKind(StrEnum):
    RETRY_STEP = "retry_step"
    RUN_COMMAND = "run_command"
    WAIT = "wait"
    MANUAL = "manual"


class ProposedAction(BaseModel):
    """A corrective action suggested by diagnosis. Never executed directly."""

    model_config = ConfigDict(extra="forbid")

    kind: ProposedActionKind
    description: str
    command: str | None = None
    risk: RiskLevel = RiskLevel.REVIEW
    evidence: list[Evidence] = Field(default_factory=list)
    delay_seconds: float = Field(default=0.0, ge=0.0, le=60.0)
    step_id: str | None = None

    @property
    def automatic(self) -> bool:
        return self.kind in (ProposedActionKind.RETRY_STEP, ProposedActionKind.WAIT)


class Diagnosis(BaseModel):
    """A structured explanation of a failed attempt."""

    model_config = ConfigDict(extra="forbid")

    category: DiagnosisCategory
    summary: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    source: str = "deterministic"
    evidence_lines: list[str] = Field(default_factory=list)
    suggested_resolution: str = ""
    proposed_actions: list[ProposedAction] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @property
    def retryable(self) -> bool:
        return any(
            action.kind in (ProposedActionKind.RETRY_STEP, ProposedActionKind.WAIT)
            for action in self.proposed_actions
        )


class ExecutionAttempt(BaseModel):
    """A recorded attempt to run one step. Stored redacted and bounded."""

    model_config = ConfigDict(extra="forbid")

    id: str
    step_id: str
    step_purpose: str
    command: str
    cwd: str = "."
    risk: RiskLevel = RiskLevel.REVIEW
    attempt_number: int = 1
    status: AttemptStatus = AttemptStatus.FAILED
    started_at: datetime
    ended_at: datetime
    duration_ms: float = 0.0
    exit_code: int | None = None
    timed_out: bool = False
    stdout_excerpt: str = ""
    stderr_excerpt: str = ""
    output_truncated: bool = False
    diagnosis: Diagnosis | None = None
    environment_keys: list[str] = Field(default_factory=list)
    previous_steps: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return self.status is AttemptStatus.SUCCEEDED


class VerificationResult(BaseModel):
    """Outcome of one readiness check."""

    model_config = ConfigDict(extra="forbid")

    success: bool
    kind: ReadinessKind
    confidence: ReadinessConfidence = ReadinessConfidence.INFERRED
    target: str = ""
    status_code: int | None = None
    detail: str = ""
    observed_at: datetime
    duration_ms: float = 0.0
    checks: int = 1
    evidence: list[Evidence] = Field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.kind.value}:{self.target}"


# -------------------------------------------------------------------- learning


class LearnedStep(BaseModel):
    """A step of the learned workflow, verified or still inferred."""

    model_config = ConfigDict(extra="forbid")

    id: str
    phase: ExecutionPhase
    purpose: str
    command: str
    cwd: str = "."
    risk: RiskLevel = RiskLevel.REVIEW
    verified: bool = False
    evidence: list[Evidence] = Field(default_factory=list)
    prerequisite: str = ""
    notes: list[str] = Field(default_factory=list)


class ReadinessRequirement(BaseModel):
    """A readiness condition learned from a verification."""

    model_config = ConfigDict(extra="forbid")

    description: str
    kind: ReadinessKind
    confidence: ReadinessConfidence
    verified: bool = False
    target: str = ""
    evidence: list[Evidence] = Field(default_factory=list)


class TroubleshootingEntry(BaseModel):
    """Failure knowledge generalized from a learning session."""

    model_config = ConfigDict(extra="forbid")

    symptom: str
    resolution: str
    category: DiagnosisCategory
    evidence: str
    attempts: list[int] = Field(default_factory=list)


class LearnedWorkflow(BaseModel):
    """Knowledge established by controlled execution, with provenance."""

    model_config = ConfigDict(extra="forbid")

    target: str
    session_id: str
    repository: str
    verified_at: datetime
    steps: list[LearnedStep] = Field(default_factory=list)
    unverified_steps: list[LearnedStep] = Field(default_factory=list)
    readiness: list[ReadinessRequirement] = Field(default_factory=list)
    troubleshooting: list[TroubleshootingEntry] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class SessionStatus(StrEnum):
    PLANNED = "planned"
    DRY_RUN = "dry_run"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    ABORTED = "aborted"
    EXECUTING = "executing"
    VERIFIED = "verified"
    FAILED = "failed"


class LearningSession(BaseModel):
    """One ``skillforge learn`` run, dry-run or real."""

    model_config = ConfigDict(extra="forbid")

    id: str
    target: str
    repository: str
    root_path: str
    status: SessionStatus = SessionStatus.PLANNED
    created_at: datetime
    completed_at: datetime | None = None
    plan: ExecutionPlan
    approved: bool = False
    approval_mode: str = ""
    attempts: list[ExecutionAttempt] = Field(default_factory=list)
    verification: VerificationResult | None = None
    learned_workflow: LearnedWorkflow | None = None
    skill_path: str | None = None
    notes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def failed_attempts(self) -> list[ExecutionAttempt]:
        return [attempt for attempt in self.attempts if not attempt.succeeded]

    @property
    def succeeded(self) -> bool:
        return self.status is SessionStatus.VERIFIED


class UpgradeResult(BaseModel):
    """What upgrading a skill with learned knowledge changed."""

    model_config = ConfigDict(extra="forbid")

    skill: str
    directory: str
    created_files: list[str] = Field(default_factory=list)
    updated_files: list[str] = Field(default_factory=list)
    status: VerificationStatus = VerificationStatus.VERIFIED
    session_id: str = ""
    notes: list[str] = Field(default_factory=list)
