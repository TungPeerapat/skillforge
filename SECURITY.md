# Security Policy

## Supported versions

| Version | Supported |
| --- | --- |
| 0.1.x | yes |
| < 0.1 | not released |

## Reporting a vulnerability

Please **do not open a public issue** for a security problem.

Use GitHub's private vulnerability reporting ("Security" → "Report a
vulnerability") or email the maintainers listed in `pyproject.toml`. Include:

- a description of the issue and its impact;
- reproduction steps (a minimal repository or command is ideal);
- the version (`skillforge --version`) and platform;
- whether you are willing to be credited.

We aim to acknowledge within 72 hours and to ship a fix or mitigation within 30
days for confirmed issues. Please give us a reasonable window before public
disclosure.

## Scope

In scope:

- commands executed by SkillForge or by generated scripts;
- secret leakage into logs, console output, generated skills, or exported files;
- path traversal, symlink escape, or writes outside the repository;
- consent bypass for external transmission of repository content;
- prompt-injection paths that cause SkillForge to treat repository text as
  instructions;
- validation bypasses that let destructive commands or credentials through.

Out of scope (by design, documented in [docs/security.md](docs/security.md)):

- commands discovered in a repository being dangerous in themselves — SkillForge
  classifies and excludes them, but does not sanitize the repository;
- malicious dependencies or lockfiles;
- an agent choosing to run a review-level command after `--confirm`;
- vulnerabilities in third-party agents that load the generated skills.

## Hardening tips for users

- Keep `security.allow_command_execution = false` (default; required).
- Keep external transmission disabled unless you trust the provider.
- Review `.skills/<name>/references/commands.md` before running a generated
  script; it lists every command with its source.
- Run `skillforge validate --strict` in CI before exporting skills.
