# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `RELEASING.md`: end-to-end runbook for pushing to GitHub and cutting a release.
- Release workflow with PyPI Trusted Publishing, tag/version/CHANGELOG checks,
  and automatic GitHub release notes (`.github/workflows/release.yml`).
- Dependabot configuration for Python packages and GitHub Actions.
- Issue forms (bug report, feature request) and a pull request template.
- CI: least-privilege permissions, per-job timeouts, coverage artifact, a
  deterministic-and-offline assertion for the demo run, and a wheel-install check.

### Fixed

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

- Command execution and sandboxed agent evaluation.
- `skillforge learn`.
- Multi-repository workspaces, tree-sitter AST analysis, MCP integration,
  interactive TUI, skill registry.
