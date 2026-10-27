"""Unit tests for the durable queue worker (no media binaries required).

The executor is replaced by an instrumented fake, so these tests exercise
claim fencing, input-channel separation, error-code mapping, the progress
sink, safe output references and the shared concurrency budget
deterministically.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import pytest

from agent.upload_store import LocalUploadStore
from agent.worker import INTERNAL_ERROR_CODE, QueueWorker, serve
from executors.ffmpeg import ExecutorInputError, ExecutorToolError
from orchestrator.queue import (
    EnqueuePolicy,
    InvalidJobStateError,
    QueuedJob,
    SQLiteQueue,
)


class FakeExecutor:
    """Deterministic executor stand-in recording every invocation."""

    def __init__(
        self,
        *,
        fail: Exception | None = None,
        delay: float = 0.0,
        output_root: Path | None = None,
    ) -> None:
        self.fail = fail
        self.delay = delay
        self.output_root = output_root
        self.calls: list[dict] = []
        self._active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def run(self, job_spec, on_progress, *, cancel_event=None):
        with self._lock:
            self._active += 1
            self.max_active = max(self.max_active, self._active)
        self.calls.append(job_spec)
        try:
            on_progress(0.0)
            if self.delay:
                time.sleep(self.delay)  # widen the overlap window
            if self.fail is not None:
                raise self.fail
            on_progress(100.0)
            if self.output_root is not None:
                out = self.output_root / job_spec["job_id"] / "part_000.mp4"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(b"segment-bytes")
                outputs = [str(out)]
            else:
                # Simulates an executor returning an unrooted absolute path.
                outputs = [f"/elsewhere/{job_spec['job_id']}/part_000.mp4"]
            return {
                "job_id": job_spec["job_id"],
                "outputs": outputs,
                "segment_count": 1,
            }
        finally:
            with self._lock:
                self._active -= 1


@pytest.fixture
def layout(tmp_path: Path):
    db = tmp_path / "q.db"
    store_dir = tmp_path / "store"
    out_dir = tmp_path / "out"
    inbox = tmp_path / "inbox"
    store_dir.mkdir()
    out_dir.mkdir()
    inbox.mkdir()
    (store_dir / "up-clip.mp4").write_bytes(b"media-bytes")
    queue = SQLiteQueue(db)
    store = LocalUploadStore(store_dir)
    yield tmp_path, queue, store, store_dir, inbox, out_dir
    queue.close()


def enqueue_upload(queue: SQLiteQueue, job_id: str, *, job_type: str = "split") -> None:
    queue.enqueue(QueuedJob(id=job_id, type=job_type, upload_id="up-clip.mp4"))


# --------------------------------------------------------------------------- #
# Happy path and queue state transitions
# --------------------------------------------------------------------------- #


def test_process_one_completes_upload_job_with_relative_output_reference(layout) -> None:
    _, queue, store, _, _, out_dir = layout
    enqueue_upload(queue, "job-1")
    executor = FakeExecutor(output_root=out_dir)
    worker = QueueWorker(queue, executor, store, output_base_dir=out_dir)

    assert worker.process_one() is True
    assert queue.size() == 0

    record = queue.fetch_job("job-1")
    assert record is not None
    assert record.status == "completed"
    # Durable record must carry a root-relative reference, never an absolute path.
    assert record.output_paths == ("job-1/part_000.mp4",)
    assert all(not Path(p).is_absolute() for p in record.output_paths)
    assert record.completed_at is not None
    # The worker hands the executor a resolved local path, never the upload id.
    assert executor.calls[0]["input_path"].endswith("up-clip.mp4")
    assert "upload://" not in executor.calls[0]["input_path"]


def test_process_one_returns_false_when_idle(layout) -> None:
    _, queue, store, *_ = layout
    worker = QueueWorker(
        queue, FakeExecutor(), store, output_base_dir=layout[5]
    )
    assert worker.process_one() is False


def test_worker_ignores_job_types_it_does_not_support(layout) -> None:
    _, queue, store, *_ = layout
    queue.enqueue(
        QueuedJob(id="t-1", type="transcription", upload_id="up-clip.mp4")
    )
    worker = QueueWorker(
        queue, FakeExecutor(), store, output_base_dir=layout[5]
    )
    assert worker.process_one() is False
    assert queue.get_status("t-1") == "queued"


def test_progress_sink_events_are_job_scoped(layout) -> None:
    _, queue, store, _, _, out_dir = layout
    enqueue_upload(queue, "job-1")
    events: list[tuple[str, float]] = []
    worker = QueueWorker(
        queue,
        FakeExecutor(output_root=out_dir),
        store,
        output_base_dir=out_dir,
        progress_sink=lambda job_id, pct: events.append((job_id, pct)),
    )
    worker.process_one()
    assert events == [("job-1", 0.0), ("job-1", 100.0)]


def test_local_input_path_channel_is_supported(layout) -> None:
    tmp_path, queue, _, store_dir, inbox, out_dir = layout
    source = inbox / "clip.mp4"
    source.write_bytes(b"x" * 10)
    policy_queue = SQLiteQueue(
        tmp_path / "q2.db",
        enqueue_policy=EnqueuePolicy(
            max_file_size_bytes=1024,
            allowed_extensions=frozenset({".mp4"}),
            allowed_base_dir=inbox,
        ),
    )
    policy_queue.enqueue(QueuedJob(id="local-1", type="split", input_path=str(source)))
    executor = FakeExecutor(output_root=out_dir)
    worker = QueueWorker(
        policy_queue,
        executor,
        LocalUploadStore(store_dir),
        output_base_dir=out_dir,
    )
    assert worker.process_one() is True
    assert executor.calls[0]["input_path"] == str(source.resolve())
    record = policy_queue.fetch_job("local-1")
    assert record is not None
    assert record.status == "completed"
    assert record.output_paths == ("local-1/part_000.mp4",)
    policy_queue.close()


# --------------------------------------------------------------------------- #
# Failure mapping
# --------------------------------------------------------------------------- #


def test_executor_input_error_marks_job_failed_with_code(layout) -> None:
    _, queue, store, *_ = layout
    enqueue_upload(queue, "job-bad")
    worker = QueueWorker(
        queue,
        FakeExecutor(fail=ExecutorInputError("bad media")),
        store,
        output_base_dir=layout[5],
    )
    assert worker.process_one() is True

    record = queue.fetch_job("job-bad")
    assert record is not None
    assert record.status == "failed"
    assert record.error_type == "INVALID_INPUT"
    # Only the stable code is stored; the message (and any path) is not.
    assert record.output_paths == ()


def test_unexpected_exception_maps_to_internal_error(layout) -> None:
    _, queue, store, *_ = layout
    enqueue_upload(queue, "job-boom")
    worker = QueueWorker(
        queue,
        FakeExecutor(fail=RuntimeError("surprise /local/path leak")),
        store,
        output_base_dir=layout[5],
    )
    worker.process_one()
    record = queue.fetch_job("job-boom")
    assert record is not None
    assert record.status == "failed"
    assert record.error_type == INTERNAL_ERROR_CODE


def test_missing_upload_object_marks_job_failed_without_crashing(layout) -> None:
    _, queue, store, *_ = layout
    queue.enqueue(QueuedJob(id="job-missing", type="split", upload_id="up-ghost.mp4"))
    worker = QueueWorker(
        queue, FakeExecutor(), store, output_base_dir=layout[5]
    )
    assert worker.process_one() is True
    record = queue.fetch_job("job-missing")
    assert record is not None
    assert record.status == "failed"
    assert record.error_type == "INVALID_INPUT"


def test_output_outside_configured_base_marks_internal_error(layout) -> None:
    _, queue, store, _, _, out_dir = layout
    enqueue_upload(queue, "job-escape")
    # No output_root: the fake returns /elsewhere/... which is not under base.
    worker = QueueWorker(queue, FakeExecutor(), store, output_base_dir=out_dir)
    assert worker.process_one() is True
    record = queue.fetch_job("job-escape")
    assert record is not None
    assert record.status == "failed"
    assert record.error_type == INTERNAL_ERROR_CODE
    assert record.output_paths == ()


def test_worker_without_output_base_fails_closed(layout) -> None:
    _, queue, store, *_ = layout
    enqueue_upload(queue, "job-nobase")
    worker = QueueWorker(queue, FakeExecutor(output_root=layout[5]), store)
    assert worker.process_one() is True
    record = queue.fetch_job("job-nobase")
    assert record is not None
    assert record.status == "failed"
    assert record.error_type == INTERNAL_ERROR_CODE


@pytest.mark.parametrize(
    "failure",
    [
        ExecutorInputError("bad media at /srv/store/up-clip.mp4"),
        RuntimeError("crash while opening /private/data/secret.mp4"),
    ],
)
def test_failure_logs_never_contain_filesystem_paths(
    layout, caplog: pytest.LogCaptureFixture, failure: Exception
) -> None:
    _, queue, store, *_ = layout
    enqueue_upload(queue, "job-leak")
    worker = QueueWorker(
        queue, FakeExecutor(fail=failure), store, output_base_dir=layout[5]
    )
    with caplog.at_level(logging.DEBUG, logger="aetherflow.worker"):
        assert worker.process_one() is True

    # The stable code may appear; exception text and local paths may not.
    assert "srv/store" not in caplog.text
    assert "private/data" not in caplog.text
    assert "secret.mp4" not in caplog.text
    record = queue.fetch_job("job-leak")
    assert record is not None and record.status == "failed"


def test_stale_claim_is_skipped_without_failure_record(layout, monkeypatch) -> None:
    _, queue, store, *_ = layout
    enqueue_upload(queue, "job-stale")

    def stale_processing(job_id, *, generation):
        raise InvalidJobStateError("generation moved")

    monkeypatch.setattr(queue, "mark_processing", stale_processing)
    worker = QueueWorker(
        queue, FakeExecutor(), store, output_base_dir=layout[5]
    )
    assert worker.process_one() is False
    record = queue.fetch_job("job-stale")
    assert record is not None and record.error_type is None


def test_failed_state_transition_when_recording_failure_is_swallowed(layout, monkeypatch) -> None:
    _, queue, store, *_ = layout
    enqueue_upload(queue, "job-race")
    monkeypatch.setattr(
        queue,
        "mark_failed",
        lambda *a, **k: (_ for _ in ()).throw(InvalidJobStateError("stale")),
    )
    worker = QueueWorker(
        queue,
        FakeExecutor(fail=ExecutorToolError("tool failed")),
        store,
        output_base_dir=layout[5],
    )
    # Must not raise: the fenced row is owned by someone else now.
    assert worker.process_one() is True


# --------------------------------------------------------------------------- #
# run() loop
# --------------------------------------------------------------------------- #


def test_run_processes_bounded_number_of_jobs(layout) -> None:
    _, queue, store, _, _, out_dir = layout
    for idx in range(3):
        enqueue_upload(queue, f"job-{idx}")
    worker = QueueWorker(
        queue, FakeExecutor(output_root=out_dir), store, output_base_dir=out_dir
    )
    processed = worker.run(poll_interval=0.01, max_jobs=2)
    assert processed == 2
    assert queue.count("completed") == 2
    assert queue.size() == 1


def test_run_stops_on_event(layout) -> None:
    _, queue, store, *_ = layout
    stop = threading.Event()
    stop.set()
    worker = QueueWorker(
        queue, FakeExecutor(), store, output_base_dir=layout[5]
    )
    assert worker.run(poll_interval=0.01, stop_event=stop) == 0


# --------------------------------------------------------------------------- #
# serve(): concurrency cap and the shared pool-wide budget
# --------------------------------------------------------------------------- #


def _seed_pool(tmp_path: Path, db_name: str, count: int) -> Path:
    db_path = tmp_path / db_name
    setup_queue = SQLiteQueue(db_path)
    for idx in range(count):
        setup_queue.enqueue(
            QueuedJob(id=f"job-{idx:02d}", type="split", upload_id="up-clip.mp4")
        )
    setup_queue.close()
    return db_path


def test_serve_respects_concurrency_cap_with_separate_queues(layout) -> None:
    tmp_path, _, store, _, _, out_dir = layout
    db_path = _seed_pool(tmp_path, "pool.db", 4)

    executor = FakeExecutor(delay=0.15, output_root=out_dir)
    processed = serve(
        lambda: SQLiteQueue(db_path),
        executor,
        concurrency=2,
        upload_store=store,
        output_base_dir=out_dir,
        poll_interval=0.02,
        max_jobs=4,
    )
    check_queue = SQLiteQueue(db_path)
    try:
        assert processed == 4
        assert check_queue.count("completed") == 4
        assert check_queue.size() == 0
    finally:
        check_queue.close()
    assert executor.max_active == 2


@pytest.mark.parametrize("max_jobs", [1, 3, 5])
def test_serve_shared_budget_never_exceeded(layout, max_jobs: int) -> None:
    tmp_path, _, store, _, _, out_dir = layout
    total_jobs = 6
    db_path = _seed_pool(tmp_path, f"budget-{max_jobs}.db", total_jobs)

    executor = FakeExecutor(delay=0.05, output_root=out_dir)
    processed = serve(
        lambda: SQLiteQueue(db_path),
        executor,
        concurrency=2,
        upload_store=store,
        output_base_dir=out_dir,
        poll_interval=0.01,
        max_jobs=max_jobs,
    )

    check_queue = SQLiteQueue(db_path)
    try:
        completed = check_queue.count("completed")
        waiting = check_queue.size()
    finally:
        check_queue.close()

    # The shared budget is the hard ceiling: no thread may claim beyond it.
    assert processed == max_jobs
    assert completed == max_jobs
    assert waiting == total_jobs - max_jobs
    assert completed + waiting == total_jobs


def test_serve_budget_of_one_processes_exactly_one_job(layout) -> None:
    tmp_path, _, store, _, _, out_dir = layout
    db_path = _seed_pool(tmp_path, "single.db", 3)
    processed = serve(
        lambda: SQLiteQueue(db_path),
        FakeExecutor(delay=0.05, output_root=out_dir),
        concurrency=3,
        upload_store=store,
        output_base_dir=out_dir,
        poll_interval=0.01,
        max_jobs=1,
    )
    check_queue = SQLiteQueue(db_path)
    try:
        assert processed == 1
        assert check_queue.count("completed") == 1
        assert check_queue.size() == 2
    finally:
        check_queue.close()
