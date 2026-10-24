"""CLI guardrail tests that need no media binary (validation-only paths)."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from agent.cli import app


def test_worker_refuses_missing_upload_store(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "worker",
            "run",
            "--db-path",
            str(tmp_path / "q.db"),
            "--upload-store",
            str(tmp_path / "missing-store"),
            "--output-dir",
            str(tmp_path / "out"),
            "--max-jobs",
            "1",
        ],
    )
    assert result.exit_code == 2
    assert "worker cannot start" in result.output


def test_local_split_rejects_relative_input(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "split",
            "relative/clip.mp4",
            "--allowed-base",
            str(tmp_path),
            "--output-dir",
            str(tmp_path / "out"),
        ],
    )
    assert result.exit_code == 2
    assert "absolute path" in result.output


def test_help_lists_both_execution_paths() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "worker" in result.output and "split" in result.output
