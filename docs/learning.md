# Learning & verification (`skillforge learn`)

v0.2 turns a generated skill into a **verified** one by executing the
repository's own workflow under explicit approval, observing what actually
happens, and writing the proven procedure back into the skill.

```
Analyze → Generate → Build execution plan → Approve → Execute safely
        → Observe → Diagnose → Retry (bounded) → Verify → Learn → Upgrade skill
```

The only supported learning target in v0.2 is **`project-runner`**.

## Quick start

```console
$ skillforge generate /path/to/project          # optional; learn creates it if missing
$ skillforge learn project-runner /path/to/project
```

`skillforge learn /path/to/project` also works and defaults to
`project-runner`. The `sf` alias is equivalent.

### Dry run

```console
$ skillforge learn project-runner --dry-run
```

Analyzes the repository, builds the execution plan, classifies every command,
prints exactly what would run — and executes/writes nothing.

### Non-interactive

```console
$ skillforge learn project-runner /path/to/project --yes --only-safe
```

`--yes` is the only way to approve non-interactively. There is no configuration
option that turns execution on: a repository cannot enable execution for itself.

## Flags

| Flag | Meaning |
| --- | --- |
| `--dry-run` | Plan and classify only; execute nothing, write nothing |
| `--yes`, `-y` | Explicit non-interactive approval (also pre-approves bounded retries) |
| `--sandbox local` | Execution backend (v0.2 supports `local` only) |
| `--step-timeout SECONDS` | Per-command timeout (default 120) |
| `--ready-timeout SECONDS` | How long to wait for readiness (default 30) |
| `--max-attempts N` | Attempts per step, 1–5 (default 3) |
| `--only-safe` | Exclude `REVIEW_REQUIRED` commands from the plan |
| `--provider` / `--allow-external-llm` | Optional LLM-assisted failure diagnosis (same consent as v0.1) |
| `--force` | Allow upgrading a skill that SkillForge did not generate |
| `--json` | Machine-readable session (requires `--yes` to execute) |

## What the plan contains

Every shell command in the plan is evidence-backed and carries its source:

```
Execution plan
 #  Phase      Command / check                   Risk             Evidence
 1  Setup      uv sync                           REVIEW_REQUIRED  uv.lock:tool.uv
 2  Services   docker compose up -d postgres     REVIEW_REQUIRED  README.md:L17
 3  Migration  alembic upgrade head              REVIEW_REQUIRED  alembic.ini
 4  Launch     uv run uvicorn app.main:app       REVIEW_REQUIRED  README.md:L23

Verification
  HTTP GET http://127.0.0.1:8000/health → expect 2xx (evidenced; README.md:L31)
```

- Destructive commands are never scheduled; they are shown as blocked.
- Commands with shell syntax or unresolved placeholders are not schedulable.
- Verification is derived from readiness evidence and never fabricated:
  `/health` is only probed when the repository provides evidence for it.

## Approval and retries

The full plan, risk summary, and evidence are shown before anything runs. The
prompt defaults to **No**. If a step fails, deterministic diagnosis explains
why; only transient failures (for example a dependency that refuses
connections while it starts) propose a delayed retry of the same step.
Configuration errors (missing environment variables, port conflicts, missing
tools) are never retried blindly. Attempts are bounded per step and per
session.

## Verification and staleness

After a successful run the existing `.skills/project-runner` is upgraded in
place (or generated first if absent):

- `references/verified-workflow.md` — only steps that actually ran and
  succeeded, with their evidence and the learning session id;
- `references/troubleshooting.md` — failures that were observed and resolved,
  generalized (no logs, paths, usernames, or secrets);
- a concise `## Verification` section in `SKILL.md`;
- `.skillforge.json` records the verification metadata: status, method,
  session, timestamp, and a repository fingerprint.

Verification metadata lives in the manifest, never in SKILL.md frontmatter, so
Agent Skills compatibility is preserved.

`skillforge list` shows the state:

```
Skill                 State      Verification
project-runner        generated  VERIFIED
test-runner           generated  UNVERIFIED
```

`--json` adds `verification.status`, `method`, `session`, and `stale`.
A verified skill becomes **STALE** when a file the workflow depends on changes
(evidence hashes), or when the recorded git commit changes (clean trees only).
Re-run `skillforge learn project-runner` to re-verify.

## Learning sessions

Each run is stored in `.skillforge/sessions/<session-id>.json`:

- structured attempts with redacted, bounded excerpts;
- diagnoses and any retry;
- the plan, the readiness result, and the learned workflow reference.

Sessions are internal (git-ignore `.skillforge/`; `skillforge init` does this)
and are never exported to agents. Exporters only ever see the final skill
bundle.

## Safety

Full details: [execution-security.md](execution-security.md). In short:

- execution requires explicit approval (interactive default No or `--yes`);
- argv-only, no shell, repository-confined working directory;
- timeouts, output caps, redaction, process-group cleanup on every exit path;
- local execution is **not a sandbox** (same user/filesystem/network);
  `--sandbox docker` is not implemented in v0.2;
- an LLM can explain a failure but can never execute anything.

## Limitations (v0.2)

- Only the `project-runner` target is implemented.
- Local execution only; no container isolation.
- One long-running launch command per plan; foreground compose commands are not
  scheduled.
- Readiness support is HTTP, TCP, log signal, and process liveness.
- Diagnosis covers common local failures; it does not modify your code.
