# Execution security model (v0.2)

SkillForge v0.2 can execute repository commands — **only** through
`skillforge learn`, only after explicit approval, and only through the gate
described here. This document is the security contract for that capability.

## The gate chain

```
DISCOVERY → RISK CLASSIFICATION → EXECUTION PLAN → USER APPROVAL
         → ISOLATION → EXECUTION
```

Nothing repository-derived becomes a process unless it has passed every stage.

| Stage | What happens | Guarantee |
| --- | --- | --- |
| **Discovery** | Read-only scanning; commands are parsed from manifests, Makefiles, Procfiles, compose files, CI, and docs | Repository text is data; nothing is interpreted as an instruction |
| **Risk classification** | Static, token/regex-based `SAFE` / `REVIEW_REQUIRED` / `DANGEROUS` | A package-script body is classified together with its invocation; no shell evaluation |
| **Execution plan** | Evidence-backed steps with stable ids, cwd, timeout, expected outcome, and readiness strategy | DANGEROUS commands are listed as excluded and can never execute; commands with shell syntax or placeholders are never scheduled |
| **Approval** | Full plan with every command, risk, and evidence source; prompt defaults to **No** | Consent is never inferred; non-interactive use requires `--yes`; `--json` refuses without `--yes` |
| **Isolation** | Local backend: argv-only, no shell, repository-confined cwd, bounded time/output | See "What local execution does *not* isolate" |
| **Execution** | Bounded subprocess groups with logging, readiness checks, and unconditional cleanup | No orphan processes on success, failure, timeout, exception, or Ctrl+C |

## Consent rules

- Interactive `skillforge learn` shows the plan and asks `Continue? [y/N]`
  (default **No**). EOF/Ctrl+D means No.
- `--dry-run` analyzes, plans, classifies, and prints — it executes nothing and
  writes nothing.
- `--yes` is the only way to approve non-interactively; it also pre-approves
  bounded diagnosed retries up to `--max-attempts`.
- `--json` without `--yes` is refused (exit 2) instead of blocking on a prompt.
- **A repository cannot enable execution.** `security.allow_command_execution = true`
  is still rejected at configuration load, and execution is only ever gated on
  per-invocation CLI decisions. A malicious `skillforge.toml` can change
  analysis settings, never execution consent.

## Risk handling

- `SAFE` and `REVIEW_REQUIRED` steps are both shown in the approval prompt.
  REVIEW steps always carry their reasons (`risk_reasons`) from the classifier.
- `DANGEROUS` commands are excluded from the plan and shown as blocked; the
  executor re-classifies every step before spawning as defence in depth.
- An LLM may *propose* corrective actions after a failure, but proposals are
  risk-classified, limited to commands already in the approved plan, and are
  never executed automatically. Retries re-run approved steps only.

## What the local backend does

`LocalExecutionBackend`:

- executes **argv lists** with `shell=False`; a command containing `&&`, `||`,
  `|`, `;`, `>`, `<`, `$(`, `${`, backticks, globs, variable expansion, or
  newlines is refused before any process is spawned;
- resolves the working directory and refuses anything outside the repository
  root (resolved-path check, symlink-aware);
- enforces a per-step timeout (default 120 s) and keeps at most 64 KiB per
  stream in bounded buffers, so output cannot exhaust memory;
- starts processes in their own process group (POSIX) / process tree (Windows)
  and terminates the whole group (`SIGTERM` → `SIGKILL`;
  `taskkill /T /F` → `kill`), including after Ctrl+C;
- records only redacted, truncated excerpts; environment **values** are never
  stored, only the platform and a note that values are excluded;
- on Windows, `.cmd`/`.bat` launchers (npm, npx, …) are invoked through
  `cmd.exe /d /s /c` **after** shell metacharacters were rejected, which is the
  same invocation modern runtimes use. PowerShell/VBScript launchers are
  refused.

## Readiness

Readiness is evidence-based, never fabricated:

- `http` — GET a **loopback** URL and expect 2xx. Refuses non-loopback hosts.
- `tcp` — connect to a loopback port.
- `log_signal` — a known startup line appears in captured logs.
- `process` — the process stays alive through a grace period (weakest).

Endpoint evidence comes from documented URLs, health paths, ports
(`--port`, compose mappings, env templates, framework defaults), and route
definitions. `/health` is only probed when the repository provides evidence
for it; a documented non-readiness URL is used for a TCP check, not an HTTP
expectation. Every plan records how strong the evidence is
(`evidenced` / `inferred` / `process_only`).

## Failure handling and retries

- Every attempt is recorded (status, exit code, duration, redacted excerpts,
  diagnosis) and bounded.
- Deterministic diagnosis covers connection-refused, port-in-use, missing
  executable/module, missing environment variable, timeout, permission denied,
  and unknown exits.
- Transient failures (`connection_refused`) propose a delayed retry of the
  **same approved step**. Configuration and environment failures are never
  auto-retried; they end the session with a manual recommendation.
- Limits: `--max-attempts` 1–5 per step (default 3) and a session-wide cap of
  30 attempts. There is no unbounded agent loop.

## Optional LLM diagnosis

Reuses the existing provider and consent architecture
(`--allow-external-llm` or `security.allow_external_transmission = true`).
Only the failing step, its redacted excerpts, and the plan's commands are sent;
repository content is wrapped as untrusted data. The model cannot execute
anything, and its proposals are advisory.

## Session storage

Learning sessions are stored in `.skillforge/sessions/<id>.json` inside the
analyzed repository:

- structured and redacted, never raw logs;
- no secrets, no environment values, no absolute machine paths in learned
  artifacts;
- `.skillforge/` should be git-ignored (`skillforge init` adds it).

## What local execution does *not* isolate

Local execution runs with **your** user account, environment, filesystem
permissions, and network access. It is **not a sandbox**. Guards are the gate
chain above, not kernel isolation. `--sandbox docker` is rejected in v0.2
because a container backend does not exist yet; the `ExecutionBackend`
abstraction is the extension point for one.

## Reporting a vulnerability

See [SECURITY.md](../SECURITY.md). Please do not open a public issue for a
vulnerability.
