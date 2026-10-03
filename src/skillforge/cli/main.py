"""SkillForge command-line interface."""

from __future__ import annotations

import sys
from pathlib import Path

import typer
from rich.console import Console

from skillforge import __version__
from skillforge.cli.state import AppState
from skillforge.logging import configure_logging

app = typer.Typer(
    name="skillforge",
    help=(
        "Turn a repository into reusable AI agent skills.\n\n"
        "Analyze a codebase, plan evidence-backed skills, generate them deterministically "
        "(no API key required), validate the result, and export it for Claude Code, Codex, "
        "OpenCode, or any Agent Skills compatible agent."
    ),
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"skillforge {__version__}")
        raise typer.Exit()


def supports_box_characters(encoding: str | None) -> bool:
    """True when the terminal encoding can render Rich's default box borders."""
    try:
        "│─╭".encode(encoding or "utf-8")
    except (UnicodeEncodeError, LookupError, TypeError):
        return False
    return True


def _force_utf8_streams() -> None:
    """Make stdout/stderr UTF-8 so box drawing cannot crash a legacy console.

    Windows still defaults to code pages such as cp1252 or cp874 for redirected
    output, and Rich renders box-drawing characters. Without this, piping
    `skillforge analyze` to a file could raise UnicodeEncodeError.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # pragma: no cover - exotic stream
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover - already bound
            continue


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the version and exit.",
    ),
    verbose: int = typer.Option(
        0, "-v", "--verbose", count=True, help="Increase log verbosity (-v, -vv)."
    ),
    debug: bool = typer.Option(False, "--debug", help="Debug logging with structured context."),
    json_output: bool = typer.Option(
        False, "--json", help="Machine-readable JSON on stdout (logs stay on stderr)."
    ),
    config: Path | None = typer.Option(
        None, "--config", help="Explicit skillforge.toml path.", show_default=False
    ),
    no_color: bool = typer.Option(False, "--no-color", help="Disable colored output."),
) -> None:
    """Global options are available on every command."""
    # Decide the frame style from the *terminal* encoding, then force UTF-8 so
    # writes never fail. Legacy code pages get ASCII frames instead of garbled
    # box-drawing characters.
    terminal_encoding = getattr(sys.stdout, "encoding", None)
    safe_boxes = not supports_box_characters(terminal_encoding)
    _force_utf8_streams()
    console = Console(no_color=no_color, safe_box=safe_boxes)
    state = AppState(
        json_mode=bool(json_output),
        verbose=int(verbose),
        debug=bool(debug),
        config_path=config,
        console=console,
    )
    ctx.obj = state
    configure_logging(verbosity=int(verbose), debug=bool(debug), json_mode=bool(json_output))
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()


def register_commands() -> None:
    """Import and attach every command module."""
    from skillforge.cli.commands import (
        analyze,
        clean,
        doctor,
        export,
        generate,
        init,
        list_cmd,
        validate,
    )
    from skillforge.cli.commands import (
        eval as eval_cmd,
    )

    for module in (init, analyze, generate, list_cmd, validate, doctor, eval_cmd, export, clean):
        module.register(app)


register_commands()


def run() -> None:  # pragma: no cover - console-script entry point
    app()


if __name__ == "__main__":  # pragma: no cover
    app()
