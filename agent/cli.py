"""AetherFlow CLI entry point.

Two explicitly separate execution paths (see ``D2_MVP/02_IMPLEMENTATION_MAP.md``):

* ``aetherflow worker run`` — the durable path: claim queued jobs created
  through the authenticated API (opaque upload ids) and process them with
  the FFmpeg executor, recording status back into SQLite.
* ``aetherflow split FILE`` — the trusted local path: run the executor
  directly on a file inside an explicitly supplied trusted base directory.
  It never touches the queue and never accepts an opaque remote id.
"""

from __future__ import annotations

import json
import signal
import threading

import typer

from executors.ffmpeg import ExecutorError

app = typer.Typer(help="AetherFlow CLI", no_args_is_help=True)
worker_app = typer.Typer(help="Durable queue worker commands", no_args_is_help=True)
app.add_typer(worker_app, name="worker")


@app.command()
def init() -> None:
    """Create local configuration and initial secrets without printing them."""
    from scripts.init_wizard import main as init_main

    init_main([])


@app.command()
def split(
    file: str = typer.Argument(..., help="Absolute path to a local media file"),
    allowed_base: str = typer.Option(
        ...,
        envvar="AETHERFLOW_ALLOWED_BASE",
        help="Trusted directory the input must resolve inside (fail-closed).",
    ),
    output_dir: str = typer.Option(
        ...,
        envvar="AETHERFLOW_OUTPUT_DIR",
        help="Directory under which per-job output folders are created.",
    ),
    segment_seconds: int = typer.Option(300, min=1, help="Target segment length in seconds"),
    timeout_seconds: float = typer.Option(3600.0, min=1.0, help="Per-job FFmpeg timeout"),
    ffmpeg: str | None = typer.Option(None, envvar="AETHERFLOW_FFMPEG"),
    ffprobe: str | None = typer.Option(None, envvar="AETHERFLOW_FFPROBE"),
) -> None:
    """Split a local file once, outside the durable queue (trusted local path)."""
    from pathlib import Path

    from agent.worker import build_executor

    source = Path(file)
    if not source.is_absolute():
        typer.echo("input file must be an absolute path", err=True)
        raise typer.Exit(code=2)

    executor = build_executor(
        upload_store_dir=allowed_base,
        output_base_dir=output_dir,
        segment_sec=segment_seconds,
        ffmpeg_cmd=ffmpeg,
        ffprobe_cmd=ffprobe,
        timeout_sec=timeout_seconds,
    )
    job_id = f"local-{source.stem}"
    try:
        result = executor.run(
            {"job_id": job_id, "input_path": str(source)},
            lambda _pct: None,
        )
    except ExecutorError as exc:
        # Generic, path-free output; the stable code tells callers the class.
        typer.echo(f"split rejected: {exc.error_code}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, sort_keys=True))


@worker_app.command("run")
def worker_run(
    db_path: str = typer.Option(
        "data/aetherflow.db",
        envvar="AETHERFLOW_DB_PATH",
        help="SQLite queue database shared with the orchestrator API.",
    ),
    upload_store: str = typer.Option(
        ...,
        envvar="AETHERFLOW_UPLOAD_STORE",
        help="Directory holding objects named by opaque upload ids.",
    ),
    output_dir: str = typer.Option(
        ...,
        envvar="AETHERFLOW_OUTPUT_DIR",
        help="Directory under which per-job output folders are created.",
    ),
    segment_seconds: int = typer.Option(300, min=1, envvar="AETHERFLOW_SEGMENT_SECONDS"),
    concurrency: int = typer.Option(1, min=1, max=8, envvar="AETHERFLOW_CONCURRENCY"),
    poll_interval: float = typer.Option(1.0, min=0.05, envvar="AETHERFLOW_POLL_INTERVAL"),
    max_jobs: int = typer.Option(
        -1,
        min=-1,
        envvar="AETHERFLOW_MAX_JOBS",
        help="Stop after N jobs; -1 runs until interrupted (one-shot tests use a bound).",
    ),
    log_level: str = typer.Option("INFO", envvar="AETHERFLOW_LOG_LEVEL"),
    ffmpeg: str | None = typer.Option(None, envvar="AETHERFLOW_FFMPEG"),
    ffprobe: str | None = typer.Option(None, envvar="AETHERFLOW_FFPROBE"),
) -> None:
    """Consume queued split jobs until the job budget is spent or interrupted."""
    from agent.upload_store import LocalUploadStore
    from agent.worker import build_executor, configure_logging, serve
    from orchestrator.queue import SQLiteQueue

    configure_logging(log_level)
    try:
        store = LocalUploadStore(upload_store)
    except Exception as exc:  # noqa: BLE001 - CLI boundary, generic safe message
        typer.echo(f"worker cannot start: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    executor = build_executor(
        upload_store_dir=upload_store,
        output_base_dir=output_dir,
        segment_sec=segment_seconds,
        ffmpeg_cmd=ffmpeg,
        ffprobe_cmd=ffprobe,
    )
    stop_event = threading.Event()

    def _request_stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)

    processed = serve(
        lambda: SQLiteQueue(db_path),
        executor,
        concurrency=concurrency,
        upload_store=store,
        output_base_dir=output_dir,
        poll_interval=poll_interval,
        max_jobs=None if max_jobs < 0 else max_jobs,
        stop_event=stop_event,
    )
    typer.echo(json.dumps({"processed": processed}))


if __name__ == "__main__":
    app()
