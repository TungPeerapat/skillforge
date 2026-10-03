# Contributing to SkillForge

Thanks for considering a contribution. This project values small, verifiable
changes over large rewrites.

## Getting set up

```bash
git clone https://github.com/skillforge/skillforge
cd skillforge
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest -q
ruff check src tests && ruff format --check src tests
mypy
```

Everything runs offline. No API key is needed for the test suite.

## Before you open a pull request

1. `pytest -q` passes (add tests for new behaviour).
2. `ruff check src tests` and `ruff format --check src tests` pass.
3. `mypy` passes.
4. `skillforge analyze examples/fastapi-demo`, `generate`, and `validate` still
   pass end to end.
5. Documentation matches behaviour. If you add a feature, update the relevant
   file in `docs/` and the README limitation list if it changes.

## Design rules

- **Deterministic first.** A feature must work without an LLM unless it is
  explicitly an LLM feature.
- **No invented commands.** Commands come from parsing repository files, and they
  carry evidence. If you cannot point at a file, do not add the command.
- **Facts, inferences, unknowns.** Use `Certainty` when building models.
- **Security boundaries are not negotiable.** Nothing in discovery may execute
  repository content; writes go through `safe_join`; external transmission needs
  consent.
- **No placeholder code.** Do not add empty modules or `TODO` implementations to
  make a tree look complete.
- **Tests accompany behaviour.** Fixture-based tests beat mocks; the fixture
  repositories exist for this purpose.
- **Keep the diff focused.** One concern per pull request.

## Adding things

| Task | Guide |
| --- | --- |
| New agent target | [docs/adding-an-exporter.md](docs/adding-an-exporter.md) |
| New LLM provider | [docs/adding-a-provider.md](docs/adding-a-provider.md) |
| New ecosystem detector | `analyzer/detectors/`: implement `detect(context) -> Detection`, register in `registry.py`, add fixture assertions |
| New skill type | add a `PlanRule`, a template in `src/skillforge/templates/skills/`, and a renderer entry in `generator/renderers.py`; the test `test_all_supported_skills_have_templates` enforces consistency |
| New validation check | add a function to `validator/checks.py` and include it in `DEFAULT_CHECKS` |

## Fixtures

Fixture repositories under `tests/fixtures/` are intentionally tiny but real:
they are scanned by the analyzer, used by the validator tests, and executed by
`skillforge eval`. When you add one, keep it under ~20 files and make the
manifests realistic. `examples/fastapi-demo` is the user-facing demo and should
stay polished.

## Commit messages

Use the imperative mood and a short scope, for example:

```
analyzer: support PEP 735 dependency groups
validator: error on unresolved command placeholders
docs: document the consent model for remote providers
```

## Code of conduct

Be respectful and assume good faith. Harassment or personal attacks are not
tolerated. Report concerns to the maintainers privately.
