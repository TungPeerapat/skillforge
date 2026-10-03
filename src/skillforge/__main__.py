"""Allow ``python -m skillforge`` to behave like the ``skillforge`` CLI."""

from __future__ import annotations

from skillforge.cli.main import app

if __name__ == "__main__":
    app()
