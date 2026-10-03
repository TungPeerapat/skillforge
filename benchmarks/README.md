# Evaluation benchmarks

SkillForge's evaluation is honest about what it measures.

## What is measured

Each scenario in `scenarios/*.toml` is executed against a real fixture repository
(`tests/fixtures/<name>` — a single source of truth, shared with the test suite).
The runner performs the full pipeline — scan → plan → generate → validate — and
reports only things it can observe:

| Metric | How it is obtained |
| --- | --- |
| `skills_generated` | number of skills the planner + generator produced |
| `commands_documented` | commands in the generated SKILL.md code blocks |
| `commands_with_provenance` | share of those commands matched to analysis evidence |
| `validation_errors` | validator errors across generated skills |
| `dangerous_commands_embedded` | risk-classifier hits inside generated skills (must be 0) |
| `max_skill_md_tokens` | estimated tokens of the largest SKILL.md body |
| `analysis_latency` / `generation_latency` | wall-clock timings of the real pipeline |

## What is *not* measured by default

Agent behaviour — task success, tokens consumed, tool calls, end-to-end latency —
requires running a coding agent in a sandbox. SkillForge v0.1 does **not** do that,
so those metrics are printed as `NOT MEASURED`. They are never estimated or invented.

To measure them you can run the comparison yourself and record the results:

1. Run the scenario's `task` with your agent **without** the generated skill.
2. Run the same task **with** the skill exported (`skillforge export --to ...`).
3. Save one JSON file per scenario and condition in `recorded/`:

```json
{
  "scenario": "fastapi-health-endpoint",
  "condition": "with-skill",
  "agent": "claude-code",
  "model": "claude-sonnet-4-5",
  "success": true,
  "tokens": 18432,
  "tool_calls": 11,
  "latency_seconds": 42.5,
  "notes": "transcript in artifacts/run-42/"
}
```

`skillforge eval` picks up `benchmarks/recorded/<scenario>[-without].json` and
reports those numbers as measured. Anything without a recording stays
`NOT MEASURED`.

## Running

```bash
skillforge eval --repo .
skillforge eval --scenario fastapi-health-endpoint --json --report eval.json
```
