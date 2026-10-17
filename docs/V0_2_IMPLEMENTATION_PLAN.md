# SkillForge v0.2 — Skill Learning & Verification Engine

> Status: implementation plan. This document is the contract for v0.2 and is
> updated if scope changes. It assumes the v0.1 architecture as shipped in
> `docs/architecture.md` and extends it — nothing is rewritten.

## 0. Goal

Evolve the pipeline from:

```
Repository → Analyze → Generate → Validate → Export
```

into:

```
Repository → Analyze → Generate → Build Execution Plan → User Approval
          → Execute Safely → Observe → Diagnose Failure → Retry
          → Verify Success → Learn Workflow → Upgrade Skill
          → Validate → Export
```

Product principle: **Do not only infer how a project runs. Verify the workflow
through controlled execution, then turn the successful procedure into reusable
knowledge.**

First (and only) learning target in v0.2: **project-runner**.

---

## 1. Existing architecture that will be reused

| Concern | Existing component | Reuse in v0.2 |
| --- | --- | --- |
| Repository profile | `analyzer.analyze_repository` → `RepositoryProfile` | Input to the execution-plan builder; never re-analyzed differently |
| Commands + provenance | `models.workflow.Command` + `Evidence` | Every execution step carries the exact evidence objects already discovered |
| Risk classification | `security.command_risk.classify_command` | Gate for plan steps, retry proposals, and LLM proposals; unchanged |
| Redaction | `security.redaction.redact_text`, `find_secrets`, `Evidence` auto-redaction | Applied to every stored stdout/stderr excerpt and every learned artifact |
| Path safety | `security.paths.safe_join`, `assert_safe_relative`, `is_absolute_local_path` | Working-directory confinement and validator path checks |
| Providers + consent | `providers.base.LLMProvider`, `providers.registry.resolve_provider` | Optional LLM diagnosis; consent gate unchanged |
| Context minimization | `context.selector.select_context` | Optional LLM diagnosis gets only the failing step + excerpts, never the whole repo |
| Skill bundle | `models.skill.SkillBundle`, `SkillManifest`, `skills.store` | Upgrade path reads/writes existing project-runner skill |
| Rendering | `generator.references` string builders, Jinja templates | Base skill generation is untouched; learned references are built as `SkillFile`s |
| Validation | `validator.checks` check families | New `check_verification`; every existing check still runs |
| CLI | Typer + `AppState` + `handle_errors` + `emit_json` | New `learn` command follows the same contract and exit codes |
| Testing | `tests/unit`, `tests/integration`, `tests/fixtures`, markers | New tests added in the same layout |

## 2. Current project-runner generation flow

1. `analyze_repository` builds `RepositoryProfile.commands` / `.workflows`.
2. `SkillPlanner` recommends `project-runner` when it finds run/setup/verify
   evidence (`planner/rules.py`).
3. `build_blueprint` (`generator/blueprint.py`) selects commands for
   `project-runner` via `SkillSelection(purposes=(SETUP, RUN, VERIFY), limit=8)`,
   excludes CI sources and dangerous commands, and records excluded ones.
4. `render_skill` renders `templates/skills/project_runner.md.j2` plus
   `references/commands.md`, `references/workflows.md`,
   `references/architecture.md`, `references/environment.md`,
   `references/evidence.md`, and scripts `preflight.py` / `run_steps.py`.
5. `write_generated_skill` writes the bundle plus `.skillforge.json` with
   per-file hashes. Nothing is executed.

**v0.2 changes nothing in this flow.** Learning starts from the same profile and
upgrades the already-generated skill afterwards.

## 3. Current command risk classification

`security/command_risk.py` performs a static, token- and regex-based
classification into `SAFE` / `REVIEW` / `DANGEROUS`:

- DANGEROUS: destructive patterns (rm -rf roots, disk writes, fork bombs,
  pipe-to-shell, infra destroy, DB resets, history rewrite, …).
- REVIEW: dependency installs, migrations, docker/compose mutations, git
  mutations, network fetches, background processes, publishes, …
- SAFE: tests, linters, builds, read-only inspection, help/version.
- Package-script bodies are classified together with the script command, so
  `npm run clean` cannot smuggle a destructive payload past the classifier.

v0.2 adds **no new levels**. It adds a hard rule: DANGEROUS commands may appear
in a plan only as excluded, non-executable entries; the executor re-classifies
every step immediately before execution as defence in depth.

## 4. Current evidence/provenance model

- `Evidence(kind ∈ {fact, inference, unknown}, source, locator, detail,
  snippet, weight)`; `snippet` is redacted and truncated on construction.
- `Command` is built only from parsers (`discovery/commands.py`) and always has
  at least one `Evidence`.
- Generated skills carry `references/evidence.md` and a `.skillforge.json`
  manifest with per-file hashes, git commit, generator mode, and confidence.
- `ProvenanceEntry` records claims per target file.

v0.2 reuses `Evidence` verbatim for execution steps, learned steps, readiness
requirements, and troubleshooting entries. Learned knowledge names the learning
session that established it.

## 5. Current provider abstraction

- `LLMProvider` protocol (`generate`, `structured_generate`, `describe`,
  `requires_network`).
- `resolve_provider` refuses remote providers without
  `--allow-external-llm` or `security.allow_external_transmission = true`;
  localhost endpoints are exempt; default is `none`.
- `SkillEnricher` validates responses against a pydantic schema and drops
  commands that are not evidence-backed.

v0.2 adds `learning/llm_diagnosis.py` which uses **this** abstraction, this
consent gate, and the same "schema-validate then evidence-check" pattern. It
never executes what the LLM proposes; proposals are re-classified, re-approved,
and re-planned.

## 6. Current validator architecture

`SkillValidator` runs 11 deterministic check families over a `ValidationTarget`
(bundle + optional directory, manifest, profile, raw SKILL.md): frontmatter,
body, size, duplicates, references, script syntax, absolute paths, secrets,
command safety, command evidence, manifest integrity.

v0.2 adds the family `check_verification` (13th checked concern, still
deterministic) and keeps every existing check. A skill can only claim
`VERIFIED` when the manifest contains execution evidence: method `execution`,
session id, timestamp, fingerprint, and the verified references actually exist.
`VERIFIED` can never be established by an LLM statement.

## 7. Current security boundaries

- DISCOVERY: read-only; secrets skipped before reading; snippets redacted;
  symlinks not followed; file-size and content budgets.
- VALIDATION: static only.
- EXECUTION: **not implemented**; `security.allow_command_execution = true` is
  rejected at configuration load.
- External transmission requires explicit consent.
- Repository text is data, never instructions.

v0.2 keeps the first two and the consent rules; it introduces execution only
through the gate in §9.

## 8. Proposed learning architecture

New package `src/skillforge/learning/` (logic) plus models in
`src/skillforge/models/learning.py` (pure data, matching the layering rules).

```
learning/
├── targets.py      # LearningTarget protocol + registry (project-runner only)
├── evidence.py     # deterministic readiness-evidence extraction from scan
├── planning.py     # RepositoryProfile → ExecutionPlan (no execution)
├── approval.py     # interactive / --yes approval; default NO
├── diagnosis.py    # deterministic failure rules → Diagnosis proposals
├── llm_diagnosis.py# optional provider-backed diagnosis (reuses consent gate)
├── fingerprint.py  # repository fingerprint (staleness)
├── generalize.py   # session → LearnedWorkflow + references rendering
├── store.py        # .skillforge/sessions/<id>.json (redacted, structured)
├── upgrade.py      # write verified artifacts into the project-runner skill
└── session.py      # LearningSession orchestration + retry loop + cleanup
```

`LearningTarget` (protocol, minimal for v0.2):

```python
class LearningTarget(Protocol):
    id: str
    def build_plan(self, profile, settings, *, options) -> ExecutionPlan: ...
    def verification_hint(self, plan) -> ReadinessPlan | None: ...
    def upgrade_skill(self, repo_root, output_dir, learned, *, force) -> UpgradeResult: ...
```

Only `ProjectRunnerTarget` is registered. `learn` refuses unknown targets with a
clear "supported in v0.2: project-runner" message. Future targets
(`project-builder`, `test-runner`, `migration-guardian`, `release-verifier`) are
listed in docs but **not implemented**.

## 9. Proposed execution architecture

New package `src/skillforge/execution/`:

```
execution/
├── base.py       # ExecutionRequest, ExecutionOutcome, ExecutionBackend ABC
├── process.py    # ManagedProcess: bounded pipes, group termination, cleanup
├── local.py      # LocalExecutionBackend
└── readiness.py  # HTTP / TCP / log-signal / process readiness strategies
```

`ExecutionBackend`:

```python
class ExecutionBackend(ABC):
    id: str
    def run(self, request: ExecutionRequest) -> ExecutionOutcome: ...       # bounded one-shot
    def start(self, request: ExecutionRequest) -> ManagedProcess: ...        # long-running
    def verify(self, readiness: ReadinessPlan, process, *, timeout) -> VerificationResult: ...
    def cleanup(self) -> None: ...
    def describe(self) -> str: ...
```

`LocalExecutionBackend` guarantees:

- **argv-only**, `shell=False`, `stdin=DEVNULL`; commands containing shell
  syntax (`&&`, `||`, `|`, `;`, `>`, `<`, `$(`, backticks, newlines) or
  unresolved placeholders are refused, never interpreted;
- optional `.cmd/.bat` / Windows resolution via `shutil.which`;
- `cwd` resolved and confined under the repository root (`safe_join` +
  resolved containment);
- per-step timeout (default 120 s) and output caps (default 64 KiB per stream,
  ring-buffered by reader threads so pipes cannot deadlock);
- environment: the current process environment is passed through so tools work,
  but **names only** are ever recorded; no env values are persisted;
- every captured excerpt passes `redact_text` before it reaches a model/session;
- process groups: `start_new_session=True` on POSIX, `CREATE_NEW_PROCESS_GROUP`
  on Windows; termination is SIGTERM → wait → SIGKILL on POSIX and
  `taskkill /T /F` → `kill()` on Windows, so grandchildren (npm → node,
  make → python) do not survive;
- `cleanup()` runs in a `finally` for success, failure, timeout, exception, and
  Ctrl+C (KeyboardInterrupt propagates only after cleanup).

## 10. Proposed verification model

`ReadinessPlan(kind, confidence, url/host/port/path, expect_status,
log_pattern, evidence, timeout, detail)` with kinds:

| Kind | Success rule | Confidence when evidence is… |
| --- | --- | --- |
| `http` | GET returns 2xx within timeout | `evidenced` (URL/path in docs/CI/compose) or `inferred` (port derived from flags/service mapping/framework default) |
| `tcp` | TCP connect succeeds | `inferred` |
| `log_signal` | a known startup line appears | `inferred` |
| `process` | process still alive after a grace period | `process_only` |

Evidence extraction (`learning/evidence.py`) reads **only retained scan
contents** and looks for:

- explicit loopback URLs in curl/health lines, restricted to health-ish paths
  (`/health`, `/healthz`, `/ready`, `/readyz`, `/live`, `/livez`, `/ping`,
  `/status`, `/up`, `/actuator/health`, `/health/live`, `/health/ready`);
- ports from `--port N`, compose application port mappings, `PORT=` env
  templates, and framework defaults;
- known startup log lines (`Application startup complete`, `Uvicorn running`,
  `Listening on`, `Server started`, …).

`/health` is **never fabricated**: if no evidence exists, the strategy falls
back to TCP, then log signal, then process-only. HTTP verification is restricted
to loopback hosts (no external calls, no consent needed).

`VerificationResult` records kind, confidence, target, status code, duration,
and whether it succeeded.

## 11. Proposed domain model changes

New `src/skillforge/models/learning.py` (pure pydantic models, no I/O):

- `VerificationStatus`: `unverified | learning | verified | failed | stale`
  (`stale` is computed, never persisted as a session claim).
- `RepositoryFingerprint(git_commit, git_dirty, file_hashes,
  workflow_fingerprint, captured_at)`.
- `SkillVerification(status, method, session_id, verified_at, fingerprint,
  summary)` — stored **only** in `.skillforge.json`, never in SKILL.md
  frontmatter (avoids unsupported frontmatter and keeps Agent Skills
  compatibility).
- `ExecutionPhase`, `StepAction`, `ExecutionStep`, `ExecutionPlan`.
- `AttemptStatus`, `ExecutionAttempt`, `ExecutionOutcome` (runtime dataclass in
  `execution/base.py`), `DiagnosisCategory`, `Diagnosis`, `ProposedAction`,
  `VerificationResult`, `ReadinessKind`, `ReadinessConfidence`,
  `ReadinessPlan`, `LearnedStep`, `ReadinessRequirement`,
  `TroubleshootingEntry`, `LearnedWorkflow`, `SessionStatus`,
  `LearningSession`.

Modified models:

- `SkillManifest` gains
  `verification: SkillVerification = SkillVerification()` (additive; old
  manifests parse unchanged).
- No frontmatter fields are added to `SkillMetadata`.

## 12. Failure/retry architecture

```
execute step → failure → deterministic diagnosis → proposed correction
            → risk classification → approval → bounded retry
```

- Every attempt is recorded with command, exit code, redacted excerpt,
  duration, timeout flag, risk, and previous-step context.
- Deterministic rules (always available offline) map stderr/stdout/exit state
  to categories: `connection_refused`, `port_in_use`, `missing_executable`,
  `missing_module`, `missing_env_var`, `timeout`, `permission_denied`,
  `nonzero_exit`, `unknown`.
- Retryable categories: `connection_refused` (transient dependency startup).
  `timeout` on setup/service steps may retry once with delay; launch readiness
  timeouts never auto-retry.
- Non-retryable categories (`port_in_use`, `missing_env_var`,
  `missing_executable`, `missing_module`, `permission_denied`) produce a
  `manual` proposal and end the session; they are never "fixed" by guessing.
- Hard limits: `max_attempts` default 3 (clamped 1–5) **per step**, plus a
  session-wide attempt cap (`max_attempts × len(executable steps)`) so no
  unbounded loop is possible.
- LLM diagnosis (optional) reuses `resolve_provider`; before transmission the
  context is redacted, minimized (failing step + bounded excerpts + plan
  summary), and marked untrusted. LLM proposals are `ProposedAction`s; they are
  re-classified, matched against repository evidence, and require approval —
  the LLM never gets a shell.
- If LLM diagnosis misbehaves or is unavailable, the deterministic path result
  is unchanged.

## 13. Sandbox strategy

- v0.2 ships **LocalExecutionBackend only**, with explicit warnings in
  `--help`, docs, and the approval prompt.
- `--sandbox local` is the only accepted value. `--sandbox docker` fails with
  "not implemented in v0.2" — no false isolation claims.
- Documented non-isolation: local execution uses the same user, environment,
  network, and filesystem permissions as SkillForge itself. Guards are
  argv-only execution, cwd confinement, risk gating, approval, timeouts,
  output caps, redaction, and cleanup — not a kernel sandbox.
- The backend ABC is the extension point for a future `DockerExecutionBackend`.

## 14. CLI design

```
skillforge learn [TARGET] [PATH] [OPTIONS]
sf learn project-runner [path]
sf learn project-runner /path/to/project
sf learn /path/to/project          # TARGET defaults to project-runner
```

Options:

| Flag | Meaning |
| --- | --- |
| `--dry-run` | Analyze + plan + classify + print; execute nothing, write nothing |
| `--yes`, `-y` | Explicit non-interactive approval (never inferred) |
| `--sandbox local` | Execution backend (docker rejected in v0.2) |
| `--step-timeout SECONDS` | Per-command timeout (default 120) |
| `--ready-timeout SECONDS` | Readiness wait budget (default 30) |
| `--max-attempts N` | Per-step retries, 1–5 (default 3) |
| `--retry-delay SECONDS` | Base delay between diagnosed retries (default 2) |
| `--only-safe` | Exclude REVIEW_REQUIRED commands from the plan |
| `--provider` / `--allow-external-llm` | Optional LLM diagnosis, same consent as v0.1 |
| `--force` | Allow upgrading a skill that is not SkillForge-generated |
| `--json` | Machine-readable session/plan/result |

Behaviour:

- No approval prompt when `--json` without `--yes`: refuse (exit 5) instead of
  blocking.
- Interactive prompt defaults to **No**; EOF/empty input means No.
- Exit codes follow `errors.py`: 5 for consent/security refusals, 1 for a
  failed learning session, 2 for usage, 0 for verified (and for dry-run).

`skillforge list` gains a Verification column (`VERIFIED`, `UNVERIFIED`,
`STALE`) and `--json` gains `verification.status`, `verification.method`,
`verification.session`, `verification.stale`.

## 15. Testing strategy

Unit tests:

- plan building: phases, ordering, stable IDs, evidence linkage, DANGEROUS
  exclusion, placeholders, only-safe filtering, compose foreground exclusion;
- gate/argv: shell-syntax refusal, quote parsing, cwd escape refusal;
- backend: timeout, output caps, redaction, exit codes, missing executable,
  process cleanup, process-group termination;
- readiness: HTTP 2xx, non-2xx, TCP, log signal, process-only, evidence
  extraction (nothing fabricated, `/health` only with evidence);
- diagnosis: every category, confidence, proposals and their risk;
- retry limits: per-step and session-wide caps;
- learning: generalization strips absolute paths/usernames/transient ids,
  troubleshooting from failed-then-resolved attempts, fingerprints/staleness;
- store: session JSON round-trip, no secrets, `.skillforge` location;
- validator: verified metadata requirements, false-VERIFIED rejection, stale
  warning;
- CLI: `--dry-run` executes nothing, missing `--yes` refuses, unknown target.

Integration tests (marked `execution` + only against trusted fixtures under
`tests/fixtures/`):

- successful `learn project-runner` on `learnable-app` → VERIFIED skill with
  `references/verified-workflow.md`, `skillforge list` shows VERIFIED,
  exports still work;
- readiness timeout on `silent-app` → FAILED, attempt recorded, no orphans;
- flaky dependency fixture → attempt 1 fails with an injected marker created by
  the test, diagnosis, retry, success (real processes);
- port unavailable and missing env var via deterministic diagnosis unit
  coverage + a scripted in-process backend for the retry loop;
- Ctrl+C simulation: a fake backend raising `KeyboardInterrupt` is cleaned up;
- dry-run on a fixture whose executable does not exist still succeeds and never
  spawns a process;
- secret in fixture logs never appears in session or skill;
- staleness: edit a fingerprinted file → `list` reports STALE;
- v0.1 regression: full generate/validate/export pipeline still passes.

CI safety: execution tests use only fixtures inside `tests/fixtures/`, run with
`sys.executable`-compatible `python` commands, use loopback ports allocated by
the test, build every server from the standard library, and never touch the
network. They carry the `execution` marker so CI can select or exclude them
explicitly.

New fixtures (stdlib-only, no internet, no paid APIs):

| Fixture | Purpose |
| --- | --- |
| `learnable-app` | happy path: `python -m app`, HTTP `/health` with README evidence |
| `flaky-dependency-app` | fails with `connection refused` until `state/dependency.ready` appears; retry then success |
| `silent-app` | starts a process that never serves `/health`; readiness timeout |
| `missing-env-app` | exits with "missing required environment variable DATABASE_URL"; manual diagnosis, no auto-retry |
| `missing-tool-app` | documented run command invokes a non-existent binary; missing-executable diagnosis |

## 16. Backwards compatibility

- v0.1 commands, output shapes, file layouts, and exit codes are unchanged;
  `learn` is additive.
- `SkillManifest` changes are additive; old manifests load with default
  `verification = unverified`.
- `skillforge list` gains a column and JSON keys; existing JSON consumers keep
  working because nothing is removed or renamed.
- `security.allow_command_execution = true` remains a configuration error;
  execution consent is per-invocation only, so repository-controlled config can
  never enable execution.
- Exported skills remain byte-compatible for agents; learned artifacts only
  add `references/*.md` files and a body section, both valid Agent Skills
  content.
- `pyproject.toml` version → `0.2.0`; `__version__` follows distribution
  metadata as before.

## 17. Security threat model (v0.2 additions)

| Threat | Mitigation |
| --- | --- |
| Malicious repo enables execution via `skillforge.toml` | Config still rejects `allow_command_execution = true`; execution needs `learn` + interactive approval or explicit `--yes` |
| Prompt injection makes the agent run commands | Plan is built from evidence only; repository text is never input to the executor; approval shows every command with risk and evidence |
| Shell injection via command strings | No shell: argv parsing, metacharacter refusal, `shell=False` |
| Path traversal from a command's `cwd` or arguments | `cwd` confined under repo root; validator rejects absolute paths in learned artifacts |
| Secret leakage into sessions/skills | `redact_text` on all excerpts; `Evidence` auto-redaction; validator secret scan; sessions are git-ignored |
| Orphan processes | Process groups, kill tree, cleanup in `finally` |
| Unbounded retries / loops | Per-step and session-wide attempt caps, clamped flags |
| Unbounded logs | 64 KiB per stream ring buffers, truncated flags |
| False VERIFIED | `check_verification` requires execution artifacts + fingerprint; staleness degrades displayed status to STALE |
| LLM hallucinated fix executes something | Proposals re-classified and evidence-checked; execution only after approval through the same gate |
| Verification SSRF | HTTP readiness is loopback-only; no consent-free external requests |

## 18. Acceptance criteria

1. All 330 v0.1 tests keep passing; new tests cover every v0.2 behavior above.
2. `skillforge learn project-runner <repo>` exists, with `--dry-run`, `--yes`,
   `--sandbox local`, bounded retries, and `--json`.
3. `--dry-run` executes nothing and writes nothing.
4. Execution requires interactive approval (default No) or explicit `--yes`.
5. DANGEROUS commands can never execute; REVIEW_REQUIRED commands are listed
   and gated.
6. All execution goes through `ExecutionBackend`; no ad-hoc subprocess calls.
7. Long-running processes are terminated on success, failure, timeout,
   exception, and Ctrl+C.
8. Readiness uses evidence, never a fabricated `/health`; confidence levels are
   reported.
9. Failed attempts and deterministic diagnoses are stored redacted in
   `.skillforge/sessions/`.
10. Retries are bounded per step and per session; every attempt is recorded.
11. Optional LLM diagnosis uses the existing provider/consent path and cannot
    execute anything directly.
12. Success produces a `LearnedWorkflow`; the existing project-runner is
    upgraded to VERIFIED with `references/verified-workflow.md` and, when
    applicable, `references/troubleshooting.md`.
13. `skillforge list` and `--json` expose UNVERIFIED / VERIFIED / STALE.
14. Learning artifacts contain no secrets, absolute machine paths, usernames,
    transient identifiers, or machine-specific defaults.
15. `skillforge validate`, `skillforge export`, and the full v0.1 pipeline keep
    working; `ruff`, `ruff format --check`, `mypy`, `pytest`, `python -m build`
    all pass.
16. Docs updated: README, CHANGELOG, architecture, security, plus new
    `docs/learning.md` and `docs/execution-security.md`.

## 19. Explicit non-goals (v0.2)

- No learning targets other than `project-runner`.
- No Docker/sandbox isolation implementation (`--sandbox docker` rejected).
- No tree-sitter analysis, multi-repository workspaces, MCP server, TUI, skill
  registry, autonomous deployment, or unrestricted shell access.
- No automatic code modification of the target repository; diagnosis may
  recommend, never rewrite.
- No execution without approval; no "learn everything" generic agent loop.
- No benchmark claims that require a real agent run.

---

## Implementation order

1. Domain models (`models/learning.py`, manifest extension) + exports.
2. `execution/` (argv, process, local backend, readiness) + unit tests.
3. `learning/` (evidence, planning, diagnosis, generalize, fingerprint, store,
   upgrade) + unit tests.
4. `LearningSession` orchestration + CLI `learn` + `list` verification status.
5. Validator `check_verification` + staleness.
6. Fixtures, integration tests, CI marker.
7. Docs, README demo, CHANGELOG, version bump.
8. Quality gates + final diff review.
