"""Template loading (Jinja2 for prose, file templates for scripts)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from skillforge.errors import ExampleError

TEMPLATES_ROOT = Path(__file__).resolve().parent.parent / "templates"


@lru_cache(maxsize=1)
def _environment() -> Environment:
    if not TEMPLATES_ROOT.is_dir():  # pragma: no cover - packaging error
        raise ExampleError(
            "Skill templates are missing from the installation; reinstall skillforge."
        )
    environment = Environment(
        loader=FileSystemLoader(str(TEMPLATES_ROOT)),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        undefined=StrictUndefined,
        autoescape=False,
    )
    environment.filters["code"] = _code_filter
    return environment


def _code_filter(value: object) -> str:
    """Render a value safely inside an inline code span."""
    text = str(value)
    return text.replace("`", "'")


def render_template(relative_path: str, **context: object) -> str:
    """Render a Jinja template from the packaged templates directory."""
    template = _environment().get_template(relative_path)
    return template.render(**context)


@lru_cache(maxsize=32)
def load_text_template(relative_path: str) -> str:
    """Load a non-Jinja template (used for generated Python scripts)."""
    path = TEMPLATES_ROOT / relative_path
    if not path.is_file():  # pragma: no cover - packaging error
        raise ExampleError(f"Template not found: {relative_path}")
    return path.read_text(encoding="utf-8")
