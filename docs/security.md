# Security model

SkillForge reads untrusted repositories. This document states what it does, what
it refuses to do, and where the guards live.

## Stage separation

| Stage | What happens | Trust level |
| --- | --- | --- |
| **DISCOVERY** | Read-only scanning, parsing, and classification. Secret files are skipped before any read. Symlinks are not followed. | Untrusted input, never executed |
| **VALIDATION** | Static checks over generated skills: secrets, dangerous commands, absolute paths, broken references, size, duplicates. | Static analysis only |
| **EXECUTION** | **Not implemented.** `security.allow_command_execution` must be `false`; setting it to `true` is rejected at configuration load. | — |

Generated scripts that *can* run commands (`run_steps.py`) are conservative:

- they only contain commands that were discovered in the repository;
- destructive commands are excluded at generation time;
- `SAFE` steps run by default, `REVIEW_REQUIRED` steps require `--confirm`;
- commands containing shell metacharacters (`&&`, `|`, `;`, `>`, `$(`, backticks)
  are printed for manual execution instead of being run through a shell;
- subprocesses are spawned with `shell=False` and an explicit argument list.

## Threat model

| Threat | Mitigation | Where |
| --- | --- | --- |
| Committed secrets leak into generated skills | Secret filenames are never read; `Evidence` redacts snippets on construction; the validator scans every generated file and errors on matches; log records pass a redaction filter | `analyzer/ignore.py`, `models/common.py`, `security/secrets.py`, `logging.py`, `validator/checks.py` |
| Destructive commands are embedded or executed | Static risk classifier (SAFE / REVIEW_REQUIRED / DANGEROUS); dangerous commands are excluded from blueprints, scripts, and skills; validator errors if one appears | `security/command_risk.py`, `generator/blueprint.py`, `validator/checks.py` |
| Prompt injection in README/CI | Repository text is never treated as instructions; injection heuristics raise repository risks; evidence snippets are redacted and quoted as data; LLM prompts wrap repository content in untrusted-data delimiters | `security/injection.py`, `analyzer/detectors/docs.py`, `generator/enrichment.py` |
| Path traversal via repository-controlled names | Every write goes through `safe_join`, which normalises, rejects `..`, absolute paths, drive letters, UNC paths, and symlink escapes | `security/paths.py` |
| Symlink escape out of the repository | Scanner skips symlinks by default (`analysis.follow_symlinks = false`); `safe_join` re-checks the resolved path | `analyzer/scanner.py`, `security/paths.py` |
| Repository content sent to a third party | Default provider is `none`. Remote providers require `--allow-external-llm` or `security.allow_external_transmission = true`. Localhost endpoints do not require consent. | `providers/registry.py` |
| API keys leaked in logs or `doctor` output | Keys are read from environment variables only; `doctor` prints `<set, redacted>`; the logging filter redacts known secret patterns | `security/redaction.py`, `providers/registry.py` |
| Malicious install hooks | npm lifecycle scripts are excluded from workflows and flagged as risks when they match destructive patterns | `analyzer/detectors/node.py` |
| Oversized/decompression attacks | `analysis.max_file_size` per file and a total content budget; JSON/TOML/YAML parsing is size-guarded and failure-tolerant | `analyzer/scanner.py`, `utils/fs.py` |
| Skill output overwritten unexpectedly | Writes refuse to overwrite unless `--force`; `clean` only removes directories containing SkillForge's manifest | `skills/store.py`, `cli/commands/clean.py` |

## What SkillForge does *not* protect against

- **A malicious repository attacking your agent through the generated skill.**
  Commands come from the repository by design. The guardrails are risk labels,
  provenance, and excluding destructive commands — not a sandbox.
- **Network isolation.** `skillforge analyze` makes no network calls, but your
  own commands might.
- **Malicious dependencies.** SkillForge does not audit lockfiles or run
  vulnerability scanners.
- **Supply-chain attacks on SkillForge itself.** It ships no install scripts.

## Reporting a vulnerability

See [SECURITY.md](../SECURITY.md). Please do not open a public issue for a
vulnerability.
