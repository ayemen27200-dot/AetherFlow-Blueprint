"""AetherFlow CLI entry point."""

from __future__ import annotations

import typer

app = typer.Typer(help="AetherFlow CLI — implementation scaffold", no_args_is_help=True)


@app.command()
def init() -> None:
    """Create local configuration and initial secrets without printing them."""
    from scripts.init_wizard import main as init_main

    init_main([])


@app.command()
def split(
    file: str = typer.Argument(..., help="Path to a local video file"),
    segment_sec: int = typer.Option(300, min=1, help="Target segment length in seconds"),
    local: bool = typer.Option(False, help="Require local processing only"),
) -> None:
    """Placeholder CLI command; the split executor is not implemented yet."""
    del file, segment_sec, local
    typer.echo(
        "Job execution is not implemented in this scaffold. Complete D2_MVP and its acceptance gate.",
        err=True,
    )
    raise typer.Exit(code=2)


if __name__ == "__main__":
    app()
