"""Unit tests for the worker-facing queue extensions.

Covers the type-filtered claim and the fenced output-publishing completion,
plus the local upload store, using isolated temporary databases and
directories. No media binaries are required for these tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.upload_store import LocalUploadStore, UploadResolutionError
from orchestrator.queue import (
    InvalidJobStateError,
    JobNotFoundError,
    QueuedJob,
    SQLiteQueue,
)


def enqueue(
    queue: SQLiteQueue, job_id: str, *, job_type: str = "split", upload_id: str | None = None
) -> None:
    queue.enqueue(
        QueuedJob(id=job_id, type=job_type, upload_id=upload_id or f"up-{job_id}")
    )


# --------------------------------------------------------------------------- #
# dequeue job-type filter
# --------------------------------------------------------------------------- #


def test_dequeue_type_filter_only_claims_supported_types(tmp_path: Path) -> None:
    queue = SQLiteQueue(tmp_path / "q.db")
    enqueue(queue, "transcription-1", job_type="transcription")
    enqueue(queue, "split-1", job_type="split")

    claimed = queue.dequeue(job_types=("split",))
    assert claimed is not None and claimed.id == "split-1"
    # The filtered queue now looks empty even though another type is queued.
    assert queue.dequeue(job_types=("split",)) is None
    assert queue.get_status("transcription-1") == "queued"


def test_dequeue_type_filter_respects_priority_order(tmp_path: Path) -> None:
    queue = SQLiteQueue(tmp_path / "q.db")
    enqueue(queue, "low", job_type="split")
    queue.enqueue(QueuedJob(id="high", type="split", priority=9, upload_id="up-high"))

    claimed = queue.dequeue(job_types=("split",))
    assert claimed is not None and claimed.id == "high"


def test_dequeue_invalid_type_filter_is_rejected(tmp_path: Path) -> None:
    queue = SQLiteQueue(tmp_path / "q.db")
    with pytest.raises(ValueError):
        queue.dequeue(job_types=())  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        queue.dequeue(job_types=("",))  # type: ignore[arg-type]


def test_dequeue_default_remains_unfiltered(tmp_path: Path) -> None:
    queue = SQLiteQueue(tmp_path / "q.db")
    enqueue(queue, "a", job_type="transcription")
    enqueue(queue, "b", job_type="split")
    assert queue.dequeue() is not None
    assert queue.dequeue() is not None
    assert queue.dequeue() is None


# --------------------------------------------------------------------------- #
# complete_job: fenced completion with verified outputs
# --------------------------------------------------------------------------- #


def test_complete_job_persists_outputs_and_timestamp(tmp_path: Path) -> None:
    queue = SQLiteQueue(tmp_path / "q.db")
    enqueue(queue, "j1")
    claimed = queue.dequeue(job_types=("split",))
    assert claimed is not None
    queue.mark_processing("j1", generation=claimed.attempt)

    queue.complete_job(
        "j1",
        generation=claimed.attempt,
        output_paths=["/out/j1/part_000.mp4", "/out/j1/part_001.mp4"],
    )

    record = queue.fetch_job("j1")
    assert record is not None
    assert record.status == "completed"
    assert record.output_paths == ("/out/j1/part_000.mp4", "/out/j1/part_001.mp4")
    assert record.completed_at is not None


def test_complete_job_rejects_empty_outputs(tmp_path: Path) -> None:
    queue = SQLiteQueue(tmp_path / "q.db")
    enqueue(queue, "j1")
    claimed = queue.dequeue(job_types=("split",))
    assert claimed is not None
    with pytest.raises(ValueError):
        queue.complete_job("j1", generation=claimed.attempt, output_paths=[])
    assert queue.get_status("j1") == "assigned"


def test_complete_job_is_fenced_by_generation(tmp_path: Path) -> None:
    queue = SQLiteQueue(tmp_path / "q.db")
    enqueue(queue, "j1")
    claimed = queue.dequeue(job_types=("split",))
    assert claimed is not None

    with pytest.raises(InvalidJobStateError):
        queue.complete_job(
            "j1", generation=claimed.attempt + 1, output_paths=["/out/x.mp4"]
        )
    assert queue.get_status("j1") == "assigned"


def test_complete_job_rejects_unclaimed_and_unknown_jobs(tmp_path: Path) -> None:
    queue = SQLiteQueue(tmp_path / "q.db")
    enqueue(queue, "j1")
    with pytest.raises(InvalidJobStateError):
        queue.complete_job("j1", generation=0, output_paths=["/out/x.mp4"])
    with pytest.raises(JobNotFoundError):
        queue.complete_job("ghost", generation=0, output_paths=["/out/x.mp4"])


# --------------------------------------------------------------------------- #
# LocalUploadStore
# --------------------------------------------------------------------------- #


def test_upload_store_resolves_valid_object(tmp_path: Path) -> None:
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    blob = store_dir / "up-clip.mp4"
    blob.write_bytes(b"media")
    store = LocalUploadStore(store_dir)

    resolved = store.resolve("up-clip.mp4")
    assert resolved == blob.resolve()


def test_upload_store_rejects_missing_object(tmp_path: Path) -> None:
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    store = LocalUploadStore(store_dir)
    with pytest.raises(UploadResolutionError):
        store.resolve("up-missing.mp4")


def test_upload_store_rejects_path_like_and_dot_only_ids(tmp_path: Path) -> None:
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    store = LocalUploadStore(store_dir)
    for bad in ("../escape.mp4", "a/b.mp4", "..", "."):
        with pytest.raises(UploadResolutionError):
            store.resolve(bad)


def test_upload_store_rejects_symlink_escaping_the_root(tmp_path: Path) -> None:
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"secret")
    (store_dir / "link.mp4").symlink_to(outside)
    store = LocalUploadStore(store_dir)

    with pytest.raises(UploadResolutionError):
        store.resolve("link.mp4")


def test_upload_store_requires_existing_directory(tmp_path: Path) -> None:
    with pytest.raises(UploadResolutionError):
        LocalUploadStore(tmp_path / "does-not-exist")
