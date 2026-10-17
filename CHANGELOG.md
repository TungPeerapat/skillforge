# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **v0.3 K6–K9 (bounded implementations)**: static artifact introspection for
  Python/Node/.NET/Go/Dart/Java; consent-gated documentation acquisition with
  pinned public DNS/TLS, robots, budgets, offline cache and revalidation;
  document normalization and same-host URL discovery; proof-aware capability
  merging; deterministic capability graph with validated hierarchy and store
  persistence. No dependency code, examples, or JavaScript are executed.
  Synthetic offline tests do not imply complete API coverage of real packages.
- `[knowledge]` network/document/page/quote budgets; project configuration cannot
  set `allow_network`. Optional `knowledge` extra supplies lazy-loaded httpx.

### Fixed

- Preserve callable parameter order instead of alphabetically sorting arguments.
- Reject artifact metadata traversal and symlink sources; fix Node declaration
  discovery under `node_modules` and Python partially-stubbed packages.
- Validate store key/file containment and manifest hashes against the registry.
- Distinguish prerelease URLs from release URLs and avoid duplicate Go `v` tag prefixes.
- Use Application Support for the macOS global knowledge data directory.

### Earlier unreleased additions

- **v0.3 K1 — deep dependency inventory**: `models/knowledge.py` (inventory
  relationship, role, scope, importance, `VersionInfo`, `KnowledgeTarget`) and
  `knowledge/inventory.py`, which classifies declared dependencies, detected
  technologies, and command-line tooling into one deterministic inventory with
  conservative exact-pin resolution (`MANIFEST_PIN` only when a manifest pins
  an exact version; ranges stay unresolved).
- **v0.3 K2 — exact version resolution**: opt-in lockfile reading
  (`ScanOptions.read_lockfiles`, `knowledge.read_lockfiles`,
  `knowledge.max_lockfile_bytes`; `analyze` stays byte-identical by default);
  `knowledge/lockfiles/` parses `uv.lock`, `poetry.lock`, `pdm.lock`,
  `Pipfile.lock`, `package-lock.json`, `pnpm-lock.yaml`, `yarn.lock` (v1 +
  Berry), `go.sum`, `packages.lock.json`/`project.assets.json`,
  `pubspec.lock`, and `gradle.lockfile` without executing anything;
  `knowledge/versions.py` provides per-ecosystem normalization/comparison
  (PEP 440 via `packaging`; semver/NuGet/Maven parsers); `knowledge/artifacts.py`
  reads installed Python metadata and Node `package.json` files, symlink-safe
  and confined to the repository; `knowledge/resolution.py` applies the
  lockfile → installed → manifest-pin ladder, records `KnowledgeConflict`s for
  disagreements (both claims kept), and inventories lockfile-only transitive
  packages without turning them into knowledge targets.
- `[knowledge]` configuration section: `max_inventory_entries`, per-package
  `priority` overrides, `read_lockfiles`, and `max_lockfile_bytes`.
- 55 new unit tests (inventory, lockfile corpus, version parsers, installed
  locators, scanner opt-in, resolution ladder/conflicts/acceptance) plus
  lockfile corpus fixtures under `tests/fixtures/lockfiles/`.

- **v0.3 K3 — official source resolution**: `knowledge/authority.py` with a
  versioned curated map (`knowledge/data/authority.toml`) covering 21 packages
  across pypi/npm/nuget/go/pub/maven, and `knowledge/sources.py` with offline
  per-ecosystem `SourceResolver`s. Candidate sources come from the curated map
  (official repository release/tag URLs, docs, API references, changelogs),
  URLs declared by installed artifacts, and caller-injected registry metadata
  snapshots — never from network access. `OfficialSourcePolicy` enforces HTTPS,
  exact/subdomain host verification (lookalikes rejected), a community-host
  denylist, and private/loopback SSRF rejection; every rejected candidate keeps
  its reason. `VersionMatchKind` distinguishes EXACT/COMPATIBLE/MAJOR_MATCH/
  LATEST_ONLY/UNKNOWN/MISMATCH, and only accepted EXACT sources are
  capability-eligible.
- 46 new unit tests (authority map, host/version matching, policy rejections,
  registry snapshot shapes, installed-artifact declarations, batch resolution)
  plus declared-URL extraction tests for Python METADATA and Node package.json.

- **v0.3 K4 — global versioned knowledge store**: `knowledge/store.py` stores
  one entry per `(ecosystem, technology, version)` under the platform data
  directory (`%LOCALAPPDATA%` / `$XDG_DATA_HOME` / `~/.local/share`, with the
  `SKILLFORGE_KNOWLEDGE_HOME` override), using the §13 layout with
  `registry.json`, atomic writes, bounded file locks (stale writers recovered),
  sha256 verification on read, `.corrupt-<ts>` quarantine instead of crashes,
  and a content-derived `store_hash` so regenerating identical knowledge reuses
  the same entry across projects. `knowledge/staleness.py` classifies a skill's
  `KnowledgeRef` as fresh/missing/hash-mismatch/corrupt. `SkillManifest` gains
  one additive optional `knowledge` field (SKILL.md frontmatter untouched), and
  `sha256_file` moves into `utils/fs.py`.
- 14 new unit tests (home resolution, round trip, version-key normalization,
  cross-project reuse, exclusive/bounded locks, stale-lock recovery, corruption
  quarantine, registry tolerance, staleness statuses).

- **v0.3 K5 — capability IR + structured index**: typed capability models
  (`Capability`, `CapabilityKind`, `CapabilityDomain`, `CapabilityParameter`,
  `CapabilitySignature`, `CapabilityRelationship`, `VersionLifecycle`);
  `knowledge/taxonomy.py` with 10 curated domains and deterministic
  keyword detection; `knowledge/capabilities.py` normalization (stable ids,
  sorted/deduplicated lists, auto domains); `knowledge/index.py` builds
  `by-name`/`by-kind`/`by-domain`/`by-parent`/`postings` maps (capped per
  token) and exposes a lazy `KnowledgeIndex` with deterministic ranking (exact
  name > qualified > suffix > prefix > token overlap; ties by kind order then
  name) and filters for name/qualified/kind/domain/parent/deprecated/
  used-in-project/query. 14 new unit tests, including a 10k-capability index
  and lookup that completes well inside the 5 s budget.

## [0.2.0] — 2026-10-04

Skill Learning & Verification Engine: SkillForge can now verify how a project
actually runs through controlled, approved execution and upgrade the generated
skill with the proven workflow.

### Added

- **Execution engine** (`execution/`): `ExecutionBackend` abstraction,
  `LocalExecutionBackend` (argv-only, no shell, repository-confined working
  directories), bounded process management with process-group/process-tree
  termination, output caps, and cleanup on every exit path (success, failure,
  timeout, exception, Ctrl+C).
- **Readiness verification**: deterministic HTTP (loopback-only), TCP, startup
  log-signal, and process-liveness strategies with `evidenced` / `inferred` /
  `process_only` confidence; `/health` is never fabricated.
- **Learning engine** (`learning/`): evidence-backed execution plans with
  stable step ids, risk gating, placeholder/shell-syntax refusal, deterministic
  failure diagnosis (connection refused, port in use, missing executable/module,
  missing environment variable, timeout, permission denied, unknown), bounded
  retries (1–5 per step, 30 per session), structured sessions, and
  generalization that strips machine-specific details.
- **`skillforge learn`**: plan → approve (default No) → execute → observe →
  diagnose → retry → verify → upgrade, with `--dry-run`, `--yes`,
  `--only-safe`, `--step-timeout`, `--ready-timeout`, `--max-attempts`,
  `--sandbox local`, and `--json`. `--json` requires `--yes`; non-interactive
  consent is never inferred.
- **Verification lifecycle**: `UNVERIFIED → LEARNING → VERIFIED / FAILED`, plus
  computed `STALE`; `references/verified-workflow.md`,
  `references/troubleshooting.md`, and a concise Verification section upgrade
  the existing project-runner skill in place.
- **Staleness**: repository fingerprints (evidence file hashes, git commit,
  workflow fingerprint) detect when a verified skill no longer reflects the
  repository.
- **Validator**: `check_verification` rejects false VERIFIED claims (method must
  be `execution`, session/timestamp/fingerprint required, verified references
  must exist) and warns when verification is stale.
- **CLI**: `skillforge list` shows VERIFIED / UNVERIFIED / STALE, including
  `--json`; optional LLM-assisted diagnosis reuses the existing provider and
  consent architecture and can never execute anything.
- CI status, license, and Python-version badges in the README.
- `RELEASING.md`: end-to-end runbook for pushing to GitHub and cutting a release.
- Release workflow with PyPI Trusted Publishing, tag/version/CHANGELOG checks,
  and automatic GitHub release notes (`.github/workflows/release.yml`).
- Dependabot configuration for Python packages and GitHub Actions.
- Issue forms (bug report, feature request) and a pull request template.
- CI: least-privilege permissions, per-job timeouts, coverage artifact, a
  deterministic-and-offline assertion for the demo run, and a wheel-install check.
- `docs/learning.md`, `docs/execution-security.md`, and
  `docs/V0_2_IMPLEMENTATION_PLAN.md`.
- Tests for the execution backend, readiness, plans, risk gates, approval
  behavior, timeouts, output limits, redaction, process cleanup, diagnosis,
  retries, generalization, staleness, sessions, upgrade, and validation, plus
  integration fixtures for successful learning, transient-dependency retry,
  readiness timeout, missing environment variable, missing executable, and
  secret non-leakage.

### Fixed

- `redact_text` no longer swallows trailing JSON punctuation when redacting
  assignments, so serialized learning sessions stay parseable.
- Execution output limits are applied per request (previously the backend
  default was always used).
- Long-running process logs are now drained with non-blocking reads, so
  startup/log readiness signals work while the process is still running.
- HTTP readiness records the last observed status code on timeout.
- Failed readiness on a launch step is diagnosed as a readiness timeout rather
  than a generic command timeout.
- Skill upgrade no longer grows a blank line per run and writes manifest hashes
  that match the on-disk `SKILL.md`.
- A documented non-readiness URL (for example `curl /api/items`) produces a TCP
  port check instead of a fabricated HTTP readiness expectation.
- CI tests failed on every runner with `ModuleNotFoundError: No module named
  'tests'`: `pytest` (as CI runs it) does not put the working directory on
  `sys.path` the way `python -m pytest` does. `pythonpath = ["."]` is now set in
  the pytest configuration, so both invocations behave identically.
- CLI output on Windows consoles with legacy code pages (for example cp874 or
  cp1252) now falls back to ASCII frames instead of printing garbled borders,
  and writing never fails with UnicodeEncodeError when output is redirected.

### Changed

- CI no longer references `secrets` in a job-level condition, which GitHub
  rejects; the optional live-provider job is manual-only and self-skips.
- `skillforge.__version__` now reads the installed distribution metadata, so the
  version lives only in `pyproject.toml`.
- `docs/architecture.md` and `docs/security.md` describe the v0.2 execution
  gate and learning pipeline.

## [0.1.0] — 2026-10-03

First usable release. Deterministic core, no API key required.

### Added

- **CLI**: `init`, `analyze`, `generate`, `list`, `validate`, `doctor`, `eval`,
  `export`, `clean`, plus the `sf` alias and `--json` / `--verbose` / `--debug`
  output modes.
- **Analyzer**: deterministic scanner with gitignore-compatible rules, secret and
  binary exclusion, symlink safety, per-file and total byte budgets; detectors for
  Python, Node/TypeScript, Go, .NET, Flutter/Dart, Java/Kotlin, Docker/Compose,
  Make/Task/Procfile, GitHub Actions, GitLab CI, databases/migrations,
  environment templates, and documentation.
- **Discovery**: command extraction from package scripts, Makefiles, Taskfiles,
  Procfiles, Compose files, Dockerfiles, CI pipelines, and documentation code
  blocks — every command carries a source and a risk level.
- **Planner**: nine rule-based skill types with reason, evidence, confidence,
  dependencies, and risks.
- **Generator**: deterministic `SKILL.md` + `references/` + `scripts/` generation
  with provenance, plus optional LLM enrichment that validates the response
  against a schema and rejects unevidenced commands.
- **Validator**: required files, frontmatter limits, body size, duplicate
  instructions, broken references, script syntax, absolute paths, secrets,
  dangerous commands, command evidence, and manifest integrity.
- **Security**: secret detection/redaction, command risk classifier, path and
  symlink guards, prompt-injection heuristics, log redaction, and a hard consent
  gate for external transmission.
- **Exporters**: Claude Code, OpenAI Codex, OpenCode, and portable Agent Skills
  layouts, verified against the published documentation.
- **Evaluation**: seven benchmark scenarios over real fixtures with deterministic
  metrics and explicit `NOT MEASURED` agent metrics; recorded runs are supported.
- **Providers**: `LLMProvider` protocol, deterministic mock provider,
  OpenAI-compatible (OpenAI/OpenRouter/local), and Anthropic adapters.
- **Context engine**: token-budgeted, category-ranked context selection with
  untrusted-content delimiters.
- **Tests**: 320+ tests, no network required; CI for lint, typing, tests on
  Linux and Windows, and package build.

### Not implemented (planned)

- Sandboxed agent evaluation (recorded transcript replay only).
- Multi-repository workspaces, tree-sitter AST analysis, MCP integration,
  interactive TUI, skill registry.

> Command execution and `skillforge learn` shipped in [0.2.0].
