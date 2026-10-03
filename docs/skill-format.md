# Skill format

SkillForge generates skills conforming to the [Agent Skills specification](https://agentskills.io/specification)
(checked against the published spec on 2026-10-03).

## Directory layout

```
skills/
└── project-runner/
    ├── SKILL.md            # required: frontmatter + instructions
    ├── references/         # loaded on demand
    │   ├── commands.md
    │   ├── workflows.md
    │   ├── architecture.md
    │   ├── environment.md
    │   └── evidence.md
    ├── scripts/            # deterministic utilities (stdlib only)
    │   ├── preflight.py
    │   └── run_steps.py
    └── .skillforge.json    # provenance manifest (SkillForge-specific)
```

## Frontmatter

Only portable fields are emitted:

```yaml
---
name: project-runner                 # required, ^[a-z0-9]+(-[a-z0-9]+)*$, ≤ 64 chars, matches directory
description: Start and verify the …   # required, ≤ 1024 chars, says what and when
license: MIT                          # optional (only if configured)
compatibility: Requires Python >=3.12, Docker   # optional, ≤ 500 chars
metadata:                             # optional string→string map
  generator: skillforge
  generator-version: 0.1.0
  source-repository: my-service
  certainty: inference
---
```

`allowed-tools` is part of the spec but is intentionally not emitted: it is
target-specific and SkillForge will not guess what an agent may use.

## Progressive disclosure

| Level | Content | When it loads |
| --- | --- | --- |
| 1 | `name` + `description` | always, for every installed skill |
| 2 | `SKILL.md` body (target < ~1,000 tokens, hard warning above 5,000) | when the agent selects the skill |
| 3 | `references/`, `scripts/`, `assets/` | only when the agent reads or runs them |

Design rules enforced by the validator:

- every referenced file must exist; unreferenced files warn;
- `SKILL.md` must stay under 500 lines (error above 1000);
- no secrets, destructive commands, absolute local paths, or unresolved
  placeholders;
- commands must be traceable to repository evidence (`references/evidence.md`);
- duplicate instruction lines warn.

## Evidence and provenance

Every command in `SKILL.md` also appears in `references/commands.md` with its
source (`package.json:scripts.dev`, `Makefile:L12`, `.github/workflows/ci.yml:jobs.test.steps[5]`)
and in `references/evidence.md` with the observation kind:

- `fact` — directly observed in the file;
- `inference` — derived by a rule (for example "FastAPI projects usually start
  with uvicorn");
- `unknown` — recorded as an open question, never presented as fact.

`.skillforge.json` records the generator version, generation mode
(`deterministic` / `hybrid`), provider, source repository, per-file hashes, and
the reason the skill was recommended. `skillforge validate` compares those hashes
and reports files that changed after generation.

## Agent compatibility notes

| Agent | Directory (project) | Frontmatter it acts on |
| --- | --- | --- |
| Claude Code | `.claude/skills/<name>/SKILL.md` | `name`, `description`, `allowed-tools`, plus `metadata` |
| OpenAI Codex | `.agents/skills/<name>/SKILL.md` | open-standard fields |
| OpenCode | `.opencode/skills/<name>/SKILL.md` (also reads `.claude/` and `.agents/`) | `name`, `description`, `license`, `compatibility`, `metadata` |
| Portable | `skills/<name>/SKILL.md` | open-standard fields |

Because the emitted frontmatter stays inside the open-standard portable core,
the same directory works for every conforming agent; the exporters differ only in
where the directory is placed.
