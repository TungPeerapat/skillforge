# Security model

SkillForge reads untrusted repositories. This document states what it does, what
it refuses to do, and where the guards live. Execution (v0.2) has its own
detailed contract: [execution-security.md](execution-security.md).

## Stage separation

| Stage | What happens | Trust level |
| --- | --- | --- |
| **DISCOVERY** | Read-only scanning, parsing, and classification. Secret files are skipped before any read. Symlinks are not followed. | Untrusted input, never executed |
| **VALIDATION** | Static checks over generated skills: secrets, dangerous commands, absolute paths, broken references, size, duplicates, verification claims. | Static analysis only |
| **EXECUTION** | `skillforge learn` only, behind an evidence-backed plan and explicit approval. DANGEROUS commands are never scheduled; argv-only, no shell, repository-confined cwd, bounded time/output, group termination. Local execution is **not a sandbox**. | Approved repository commands, gated |

Generated scripts that *can* run commands (`run_steps.py`, `knowledge` scripts)
are conservative:

- they only contain commands that were discovered in the repository;
- destructive commands are excluded at generation time;
- `SAFE` steps run by default, `REVIEW_REQUIRED` steps require `--confirm`;
- commands containing shell metacharacters (`&&`, `|`, `;`, `>`, `$(`, backticks)
  are printed for manual execution instead of being run through a shell;
- subprocesses are spawned with `shell=False` and an explicit argument list.

The `skillforge learn` execution path is stricter than the generated scripts:

- a complete plan is shown with every command, risk, and evidence source;
- approval defaults to **No**, `--json` requires `--yes`, and a repository
  cannot enable execution through configuration;
- DANGEROUS commands are never scheduled and are re-classified before spawning;
- readiness probes never leave the loopback interface;
- processes are terminated as a group on every exit path, including Ctrl+C;
- EOL/Ctrl+D at the approval prompt means No.

## Threat model

| Threat | Mitigation | Where |
| --- | --- | --- |
| Committed secrets leak into generated skills | Secret filenames are never read; `Evidence` redacts snippets on construction; the validator scans every generated file and errors on matches; log records pass a redaction filter | `analyzer/ignore.py`, `models/common.py`, `security/secrets.py`, `logging.py`, `validator/checks.py` |
| Destructive commands are embedded or executed | Static risk classifier (SAFE / REVIEW_REQUIRED / DANGEROUS); dangerous commands are excluded from blueprints, scripts, and skills; validator errors if one appears; the learning planner excludes them and the executor re-classifies every step | `security/command_risk.py`, `generator/blueprint.py`, `learning/planning.py`, `validator/checks.py` |
| Prompt injection in README/CI | Repository text is never treated as instructions; injection heuristics raise repository risks; evidence snippets are redacted and quoted as data; LLM prompts wrap repository content in untrusted-data delimiters | `security/injection.py`, `analyzer/detectors/docs.py`, `generator/enrichment.py` |
| Malicious repository enables execution | `security.allow_command_execution = true` is rejected at config load; execution requires interactive approval or explicit `--yes`; `--json` refuses without `--yes` | `config.py`, `cli/commands/learn.py` |
| Shell injection through discovered commands | Argv-only execution; shell metacharacters, expansions, globs, and placeholders are refused before spawning; Windows batch launchers go through `cmd.exe` only after that rejection | `security/argv.py`, `execution/local.py` |
| Working-directory escape | Every step's cwd is resolved and confined to the repository root; symlinks are rejected | `execution/local.py` |
| Orphan processes after failure/timeout/Ctrl+C | Process groups (POSIX) / process trees (Windows); cleanup in `finally` on every exit path | `execution/process.py`, `learning/session.py` |
| Unbounded output or retries | 64 KiB per stream with bounded reader buffers; per-step attempts 1–5 and a session-wide attempt cap | `execution/process.py`, `learning/session.py` |
| Path traversal via repository-controlled names | Every write goes through `safe_join`, which normalises, rejects `..`, absolute paths, drive letters, UNC paths, and symlink escapes | `security/paths.py` |
| Symlink escape out of the repository | Scanner skips symlinks by default (`analysis.follow_symlinks = false`); `safe_join` re-checks the resolved path | `analyzer/scanner.py`, `security/paths.py` |
| Repository content sent to a third party | Default provider is `none`. Remote providers require `--allow-external-llm` or `security.allow_external_transmission = true`. Localhost endpoints do not require consent. LLM diagnosis sends only redacted failure context | `providers/registry.py`, `learning/llm_diagnosis.py` |
| API keys leaked in logs or `doctor` output | Keys are read from environment variables only; `doctor` prints `<set, redacted>`; the logging filter redacts known secret patterns; session excerpts are redacted before storage | `security/redaction.py`, `providers/registry.py`, `learning/session.py` |
| Malicious install hooks | npm lifecycle scripts are excluded from workflows and flagged as risks when they match destructive patterns | `analyzer/detectors/node.py` |
| Oversized/decompression attacks | `analysis.max_file_size` per file and a total content budget; JSON/TOML/YAML parsing is size-guarded and failure-tolerant; execution output is capped | `analyzer/scanner.py`, `utils/fs.py`, `execution/process.py` |
| Skill output overwritten unexpectedly | Writes refuse to overwrite unless `--force`; `clean` only removes directories containing SkillForge's manifest | `skills/store.py`, `cli/commands/clean.py` |
| False VERIFIED claims | VERIFIED requires execution evidence (session, timestamp, fingerprint, verified references); the validator errors otherwise and the fingerprint marks stale skills | `validator/checks.py`, `learning/upgrade.py` |

## What SkillForge does *not* protect against

- **A malicious repository attacking your agent through the generated skill.**
  Commands come from the repository by design. The guardrails are risk labels,
  provenance, approval, and excluding destructive commands — not a sandbox.
- **Network isolation.** Static analysis makes no network calls, but approved
  commands and your own tools might.
- **Malicious dependencies.** SkillForge does not audit lockfiles or run
  vulnerability scanners.
- **Local execution isolation.** `skillforge learn` runs with your user
  account, filesystem permissions, and network access when you approve it.
- **Supply-chain attacks on SkillForge itself.** It ships no install scripts.

## Reporting a vulnerability

See [SECURITY.md](../SECURITY.md). Please do not open a public issue for a
vulnerability.
