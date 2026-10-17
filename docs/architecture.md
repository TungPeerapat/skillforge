# Architecture

## Pipeline

```mermaid
flowchart TD
    A[Repository] -->|scan| B[Scanner<br/>ignore rules, secret exclusion,<br/>classification, byte budgets]
    B --> C[Detectors<br/>Python, Node, Go, .NET, Flutter, Java,<br/>infra, CI, database, env, docs]
    C --> D[Discovery<br/>commands + workflows<br/>with evidence]
    D --> E[Planner<br/>9 rule-based skill types]
    E --> F[Generator<br/>blueprint → templates]
    F --> G[Validator<br/>12 check families]
    G --> H[(Skill bundle)]
    H --> I[Exporters<br/>Claude Code · Codex · OpenCode · portable]

    H -. optional, consent required .-> J[Context selector<br/>token budget]
    J --> K[LLM provider]
    K --> L[Enrichment<br/>schema + evidence check]
    L --> F

    H -. v0.2 learning .-> M[Execution plan<br/>evidence-backed steps]
    M --> N{User approval}
    N -->|approved| O[Execution backend<br/>argv-only, bounded,<br/>process-group cleanup]
    O --> P[Readiness verification<br/>HTTP / TCP / log / process]
    P --> Q[Diagnosis + bounded retry]
    Q --> O
    P -->|verified| R[Generalize<br/>LearnedWorkflow]
    R --> S[Upgrade skill<br/>verified-workflow.md + manifest]
    S --> G
    R --> T[(.skillforge/sessions)]

    H --> U[Evaluation<br/>deterministic checks +<br/>honest NOT MEASURED]
```

Every arrow is a Python package with a narrow interface. The deterministic
column (left) is the product; the optional column (right) is an enhancement that
can be removed without breaking anything. Execution (v0.2) is only reachable
through the approval gate and never affects static analysis.

## Layers and dependency direction

```
cli  →  services (analyzer, planner, generator, validator, evaluation, doctor,
                 execution, learning)
             ↓
     models (pydantic, pure data)      security (pure functions)
             ↓                               ↓
                        utils (no domain knowledge)
```

- `models` never imports services, never touches the filesystem.
- `security` depends only on `models` and `utils`. It has no I/O.
- `analyzer` reads the filesystem (through the scanner) but never writes and never
  executes repository content.
- `generator` renders from data. It receives everything it needs through a
  blueprint; it does not re-analyze.
- `exporters` transform a `SkillBundle`; they cannot generate content, which
  keeps agent-specific behaviour out of the core.
- `providers` are injected. No module imports an SDK directly; `httpx` is
  imported lazily inside the HTTP adapters.
- `execution` is the **only** package that spawns repository processes. It is
  argv-only, bounded, and consumes plans produced by `learning`.
- `learning` orchestrates planning, approval, execution, diagnosis, and the
  upgrade of verified skills. It never writes skills without execution
  evidence.

## Data flow for one command

`skillforge analyze examples/fastapi-demo`:

1. `cli` resolves the repo root and loads `Settings` (defaults → user config →
   `skillforge.toml` → `SKILLFORGE_*` env vars → CLI flags).
2. `ScanResult` holds file records, retained contents, skip reasons, and Git info.
   Secrets are excluded before reading; snippets are redacted at model
   construction.
3. Detectors run in a fixed order and each returns a `Detection`. Failures inside
   one detector become warnings, never crashes.
4. `build_profile` merges detections (dedupe rules are unit-tested), synthesizes
   workflows, and produces a `RepositoryProfile` with `FACT`/`INFERENCE`/`UNKNOWN`
   certainty attached.
5. `SkillPlanner` evaluates nine rules; each returns a `SkillCandidate` with
   reason, evidence, confidence, dependencies, risks, priority.
6. `SkillGenerator` builds a `SkillBlueprint` per candidate, renders `SKILL.md`
   from Jinja templates, generates references and deterministic scripts, and
   records provenance for every command.
7. `SkillValidator` runs the check families against the in-memory bundle before
   anything is written, and again after writing when `skillforge validate` runs.

## Determinism

Analysis output is deterministic for a fixed repository and configuration:
walking is lexical, detectors are ordered, merges use stable sort keys, and no
timestamps appear inside generated skills (they live in the manifest). The test
suite asserts byte-identical regeneration and byte-identical analysis across
runs, excluding `generated_at`.

## Extension points

| Extension | Where | Contract |
| --- | --- | --- |
| New ecosystem detector | `analyzer/detectors/` | `EcosystemDetector.detect(context) -> Detection` |
| New skill type | `planner/rules.py` + `generator/renderers.py` + template | `PlanRule.evaluate(context) -> SkillCandidate \| None` |
| Smarter file ranking | `context/selector.py` | `FileRanker.rank(path, category) -> int` |
| New agent target | `exporters/` | `BaseExporter.skills_root(repo_root) -> Path` |
| New LLM vendor | `providers/` | `LLMProvider` protocol |
| Real agent evaluation | `evaluation/executors.py` | `AgentExecutor.run(scenario, task, condition)` |
| New execution backend | `execution/` | `ExecutionBackend.run/start/verify/cleanup/describe` |
| New learning target | `learning/targets.py` | `LearningTarget.build_plan` + `upgrade` |
| Smarter readiness | `learning/evidence.py`, `execution/readiness.py` | `ReadinessPlan` + probe strategies |
| AST analysis | `context/selector.py` ranker, `analyzer/detectors/` | not implemented in v0.2 |

## Known architectural limitations (v0.2)

- Go, Java, .NET, and Flutter detectors read the root manifest layout; a
  multi-module Go/Java repository is only partially analysed. Python, Node, and
  .NET handle per-directory manifests.
- Workflow synthesis is rule-based; it does not build a full task dependency
  graph.
- No file watching, caching, or incremental analysis.
- Execution is local only (no container sandbox) and the only learning target
  is `project-runner`.
- Diagnosis is deterministic plus optional LLM assistance; SkillForge never
  rewrites the target repository.
- `skillforge learn` verifies workflows through controlled execution; static
  analysis alone always yields `UNVERIFIED` skills.
