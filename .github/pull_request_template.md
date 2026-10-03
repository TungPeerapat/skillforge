## What and why

<!-- One paragraph: what changes, and what problem it solves. -->

## How it was verified

<!-- Commands run, fixtures used, screenshots/JSON output if relevant. -->

## Checklist

- [ ] `pytest -q` passes
- [ ] `ruff check src tests` and `ruff format --check src tests` pass
- [ ] `mypy` passes
- [ ] `skillforge analyze/generate/validate examples/fastapi-demo` still works
- [ ] Tests were added or updated for the behaviour change
- [ ] Documentation updated (README, `docs/`, or `CHANGELOG.md` if user-visible)
- [ ] No secrets, machine-specific paths, or destructive commands added
- [ ] Deterministic behaviour preserved (or the change is guarded behind a flag)

## Risk / blast radius

<!-- Which parts of the pipeline does this touch? What could regress? -->

## Related issues

<!-- Closes #… -->
