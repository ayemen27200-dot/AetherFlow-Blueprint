"""End-to-end G2 pipeline tests: API -> durable queue -> worker -> FFmpeg.

These tests exercise the full acceptance path with a real FastAPI
application, a real SQLite database file, a real worker and a real FFmpeg
binary against the committed synthetic fixture. No network, paid resources
or private media are involved; the tests skip automatically when no FFmpeg
binary is available.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from agent.cli import app as cli_app
from agent.upload_store import LocalUploadStore
from agent.worker import QueueWorker
from executors.ffmpeg import FFmpegSplitExecutor
from orchestrator.main import app, get_settings
from orchestrator.queue import SQLiteQueue

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "sample_5s.mp4"
TOKEN = "g2-e2e-bearer-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
UPLOAD_ID = "up-sample-5s.mp4"


def _ffmpeg() -> str | None:
    path = shutil.which("ffmpeg")
    if path:
        return path
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


FFMPEG = _ffmpeg()
pytestmark = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg binary not available")


def _decode_clean(path: Path) -> None:
    completed = subprocess.run(
        [FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-xerror",
         "-i", str(path), "-f", "null", "-"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0 and not completed.stderr.strip(), completed.stderr


def _make_corrupt(source: Path, target: Path) -> None:
    data = bytearray(source.read_bytes())
    middle = len(data) // 2
    data[middle : middle + 4096] = b"\x00" * 4096
    target.write_bytes(data)


@pytest.fixture
def environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "data" / "aetherflow.db"
    store_dir = tmp_path / "upload_store"
    output_dir = tmp_path / "outputs"
    store_dir.mkdir(parents=True)
    output_dir.mkdir()
    shutil.copy2(FIXTURE, store_dir / UPLOAD_ID)

    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("BEARER_TOKEN", TOKEN)
    get_settings.cache_clear()

    with TestClient(app) as client:
        yield tmp_path, client, db_path, store_dir, output_dir
    get_settings.cache_clear()


def _worker(db_path: Path, store_dir: Path, output_dir: Path) -> QueueWorker:
    queue = SQLiteQueue(db_path)
    store = LocalUploadStore(store_dir)
    executor = FFmpegSplitExecutor.from_config(
        allowed_base_dir=store_dir,
        output_base_dir=output_dir,
        segment_sec=3,
        ffmpeg_cmd=FFMPEG,
        ffprobe_cmd=shutil.which("ffprobe"),
    )
    return QueueWorker(
        queue, executor, store, output_base_dir=output_dir
    )


def _assert_no_absolute_path_leak(references: list[str]) -> None:
    """Public records must only expose root-relative output references."""
    assert references, "completed job must publish output references"
    for ref in references:
        assert not Path(ref).is_absolute(), f"absolute path leaked: {ref}"
        assert ".." not in Path(ref).parts
        assert not ref.startswith(("/", "\\"))


def _resolve_reference(output_dir: Path, ref: str) -> Path:
    """Resolve a public output reference back to the real file for verification."""
    path = (output_dir / ref).resolve()
    # The resolved verification target must stay inside the private output root.
    assert path.is_relative_to(output_dir.resolve())
    return path


def _create_job(client: TestClient, upload_id: str) -> str:
    response = client.post(
        "/jobs", headers=AUTH, json={"type": "split", "file": upload_id, "priority": 5}
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "queued" and body["id"]
    return body["id"]


# --------------------------------------------------------------------------- #
# Full happy path
# --------------------------------------------------------------------------- #


def test_g2_create_process_and_verify_real_split(environment) -> None:
    _, client, db_path, store_dir, output_dir = environment
    job_id = _create_job(client, UPLOAD_ID)
    assert client.get(f"/jobs/{job_id}", headers=AUTH).json()["status"] == "queued"

    worker = _worker(db_path, store_dir, output_dir)
    assert worker.process_one() is True
    assert worker.run(max_jobs=1) == 0  # queue is drained, no second claim
    worker._queue.close()

    record = client.get(f"/jobs/{job_id}", headers=AUTH).json()
    assert record["status"] == "completed"
    assert record["error_type"] is None
    references = record["output_paths"]
    _assert_no_absolute_path_leak(references)
    # The storage root must never appear in any public job representation.
    body = client.get("/jobs", headers=AUTH).text
    assert str(output_dir) not in body and str(store_dir) not in body

    for ref in references:
        # Public reference -> private file resolved only inside the test.
        path = _resolve_reference(output_dir, ref)
        assert path.parent.name == job_id
        assert path.is_file() and path.stat().st_size > 0
        _decode_clean(path)  # independent re-decode, not the executor's word

    listed = client.get("/jobs", headers=AUTH, params={"status": "completed"}).json()
    listed_job = next(job for job in listed if job["id"] == job_id)
    _assert_no_absolute_path_leak(listed_job["output_paths"])


def test_g2_path_like_upload_id_is_rejected_at_the_api(environment) -> None:
    _, client, _, _, _ = environment
    response = client.post(
        "/jobs", headers=AUTH, json={"type": "split", "file": "../etc/passwd"}
    )
    assert response.status_code == 400
    assert client.get("/jobs", headers=AUTH).json() == []


# --------------------------------------------------------------------------- #
# Failure paths
# --------------------------------------------------------------------------- #


def test_g2_corrupt_upload_marks_job_failed_with_safe_code(environment) -> None:
    tmp_path, client, db_path, store_dir, output_dir = environment
    _make_corrupt(store_dir / UPLOAD_ID, store_dir / "up-corrupt.mp4")
    job_id = _create_job(client, "up-corrupt.mp4")

    worker = _worker(db_path, store_dir, output_dir)
    assert worker.process_one() is True
    worker._queue.close()

    record = client.get(f"/jobs/{job_id}", headers=AUTH).json()
    assert record["status"] == "failed"
    assert record["error_type"] == "INVALID_INPUT"
    assert record["output_paths"] == []
    # No partial output survives and no filesystem path leaked into the record.
    assert not (output_dir / job_id).exists()
    assert "/" not in record["error_type"]


def test_g2_missing_upload_object_fails_cleanly(environment) -> None:
    _, client, db_path, store_dir, output_dir = environment
    job_id = _create_job(client, "up-ghost.mp4")  # valid token, no stored object

    worker = _worker(db_path, store_dir, output_dir)
    assert worker.process_one() is True
    worker._queue.close()

    record = client.get(f"/jobs/{job_id}", headers=AUTH).json()
    assert record["status"] == "failed"
    assert record["error_type"] == "INVALID_INPUT"
    assert not (output_dir / job_id).exists()


# --------------------------------------------------------------------------- #
# Durability across a simulated worker restart
# --------------------------------------------------------------------------- #


def test_g2_queued_job_survives_worker_restart(environment) -> None:
    _, client, db_path, store_dir, output_dir = environment
    job_id = _create_job(client, UPLOAD_ID)

    # A first worker process opens the queue and exits without claiming.
    first_queue = SQLiteQueue(db_path)
    assert first_queue.get_status(job_id) == "queued"
    first_queue.close()

    # A brand-new worker (new process equivalent) picks up the durable row.
    worker = _worker(db_path, store_dir, output_dir)
    assert worker.process_one() is True
    worker._queue.close()

    record = client.get(f"/jobs/{job_id}", headers=AUTH).json()
    assert record["status"] == "completed"
    references = record["output_paths"]
    _assert_no_absolute_path_leak(references)
    for ref in references:
        path = _resolve_reference(output_dir, ref)
        assert path.is_file() and path.stat().st_size > 0
        _decode_clean(path)


# --------------------------------------------------------------------------- #
# CLI surfaces (real binary)
# --------------------------------------------------------------------------- #


def test_g2_cli_worker_processes_one_queued_job(environment) -> None:
    _, client, db_path, store_dir, output_dir = environment
    job_id = _create_job(client, UPLOAD_ID)

    result = CliRunner().invoke(
        cli_app,
        [
            "worker", "run",
            "--db-path", str(db_path),
            "--upload-store", str(store_dir),
            "--output-dir", str(output_dir),
            "--segment-seconds", "3",
            "--max-jobs", "1",
            "--ffmpeg", FFMPEG,
        ],
    )
    assert result.exit_code == 0, result.output
    record = client.get(f"/jobs/{job_id}", headers=AUTH).json()
    assert record["status"] == "completed"
    _assert_no_absolute_path_leak(record["output_paths"])
    for ref in record["output_paths"]:
        _decode_clean(_resolve_reference(output_dir, ref))


def test_g2_cli_local_split_uses_trusted_path(environment) -> None:
    tmp_path, _, _, store_dir, output_dir = environment
    local_out = tmp_path / "local_out"
    local_out.mkdir()
    result = CliRunner().invoke(
        cli_app,
        [
            "split",
            str(store_dir / UPLOAD_ID),
            "--allowed-base", str(store_dir),
            "--output-dir", str(local_out),
            "--segment-seconds", "3",
            "--ffmpeg", FFMPEG,
        ],
    )
    assert result.exit_code == 0, result.output
    import json

    payload = json.loads(result.stdout.strip())
    assert payload["segment_count"] >= 1
    for part in payload["outputs"]:
        _decode_clean(Path(part))


def test_g2_cli_local_split_rejects_relative_path(environment) -> None:
    tmp_path, _, _, store_dir, output_dir = environment
    result = CliRunner().invoke(
        cli_app,
        [
            "split",
            "relative/clip.mp4",
            "--allowed-base", str(store_dir),
            "--output-dir", str(output_dir),
        ],
    )
    assert result.exit_code == 2
