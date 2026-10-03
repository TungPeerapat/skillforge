# Adding an exporter

Exporters adapt an already-generated skill to one agent's directory layout. They
must not generate or rewrite content.

## 1. Implement the exporter

```python
# src/skillforge/exporters/adapters.py
from pathlib import Path

from skillforge.exporters.base import BaseExporter


class MyAgentExporter(BaseExporter):
    id = "myagent"
    display_name = "My Agent"
    description = "Writes .myagent/skills/<name>/ (or ~/.myagent/skills with --global)."
    skills_subdir = ".myagent/skills"

    def skills_root(self, repo_root: Path) -> Path:
        if self._global_scope:
            return Path.home() / ".myagent" / "skills"
        return repo_root / ".myagent" / "skills"

    def compatibility_notes(self) -> list[str]:
        # Facts you verified against the agent's documentation, with the date.
        return [
            "My Agent discovers skills in .myagent/skills/<name>/SKILL.md "
            "(verified against docs on YYYY-MM-DD).",
        ]
```

`BaseExporter` provides `export()`, `export_generated()`, `export_directory()`,
`discover()`, safe path handling, manifest emission, and `--force` semantics.

## 2. Register it

```python
# src/skillforge/exporters/registry.py
_EXPORTERS = (ClaudeCodeExporter, CodexExporter, OpenCodeExporter, PortableExporter, MyAgentExporter)

EXPORTER_ALIASES = {
    ...,
    "myagent": "myagent",
}
```

## 3. Test it

Add a case to `tests/unit/test_exporters.py`:

```python
@pytest.mark.parametrize(
    ("exporter_class", "expected_subdir"),
    [
        ...,
        (MyAgentExporter, ".myagent/skills"),
    ],
)
```

The existing parametrised test verifies the layout, the manifest, and the
frontmatter. `test_exported_frontmatter_is_portable` guards against emitting
fields that other agents ignore.

## Rules

1. **Do not invent a format.** Read the agent's current documentation and record
   what you verified in `compatibility_notes()` with a date.
2. **Do not change `SKILL.md`.** If an agent needs different frontmatter, emit an
   extra file instead, or discuss changing the core model first.
3. **Never execute anything.** Exporters copy bytes.
4. **Stay inside `skills_root()`.** All writes must go through `safe_join`.
5. **Keep `--force` semantics.** Refuse to overwrite without it.
