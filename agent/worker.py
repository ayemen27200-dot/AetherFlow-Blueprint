"""Durable queue worker linking claimed jobs to the FFmpeg executor.

The worker is the only component that turns a claimed queue row into local
processing:

* claims are atomic (``SQLiteQueue.dequeue``) and type-filtered so this
  worker never accepts a job type it cannot execute;
* every state transition presents the claim generation (fencing), so a
  stale worker cannot overwrite a newer claim;
* opaque ``upload_id`` jobs are resolved through :class:`LocalUploadStore`,
  while validated local ``input_path`` jobs pass straight to the executor,
  keeping the opaque and local channels separate;
* failures are recorded as stable error codes, never with paths or tool
  output, and the executor itself discards partial output on failure.

Concurrency: each worker thread opens its own ``SQLiteQueue`` instance via
the supplied factory, as the queue documentation requires. Atomic
``dequeue`` guarantees a job is never processed by two threads at once.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Sequence
from pathlib import Path

from agent.upload_store import LocalUploadStore, UploadResolutionError
from executors.ffmpeg import ExecutorError, FFmpegSplitExecutor
from orchestrator.queue import (
    InvalidJobStateError,
    JobNotFoundError,
    QueuedJob,
    SQLiteQueue,
)

logger = logging.getLogger("aetherflow.worker")

# Error code for defects in the worker itself rather than the media input.
INTERNAL_ERROR_CODE = "INTERNAL_ERROR"
DEFAULT_SUPPORTED_TYPES: tuple[str, ...] = ("split",)

ProgressSink = Callable[[str, float], None]
QueueFactory = Callable[[], SQLiteQueue]


class _JobBudget:
    """Shared, lock-protected processing budget for a worker pool.

    A token is acquired *before* each claim and permanently consumed when a
    job is actually processed; an idle (empty-queue) claim attempt returns its
    token so the budget always counts processed jobs, never polling attempts.
    With multiple threads this guarantees the pool as a whole cannot exceed
    ``limit``, unlike a per-thread quota which multiplies the cap.
    """

    def __init__(self, limit: int | None) -> None:
        if limit is not None and limit < 0:
            raise ValueError("max_jobs must be non-negative")
        self._limit = limit
        self._claimed = 0
        self._lock = threading.Lock()

    def acquire(self) -> bool:
        """Reserve one processing slot, or return ``False`` when exhausted."""
        with self._lock:
            if self._limit is not None and self._claimed >= self._limit:
                return False
            self._claimed += 1
            return True

    def release(self) -> None:
        """Give back a token whose claim attempt processed no job."""
        with self._lock:
            if self._claimed > 0:
                self._claimed -= 1


class QueueWorker:
    """Process claimed jobs from one durable queue using one executor."""

    def __init__(
        self,
        queue: SQLiteQueue,
        executor: FFmpegSplitExecutor,
        upload_store: LocalUploadStore | None = None,
        *,
        output_base_dir: str | Path | None = None,
        supported_types: Sequence[str] = DEFAULT_SUPPORTED_TYPES,
        progress_sink: ProgressSink | None = None,
    ) -> None:
        self._queue = queue
        self._executor = executor
        self._upload_store = upload_store
        self._output_base = (
            Path(output_base_dir).resolve() if output_base_dir is not None else None
        )
        self._supported_types = tuple(supported_types)
        self._progress_sink = progress_sink

    def process_one(self) -> bool:
        """Claim and process a single job. Returns ``False`` when idle.

        A processed job — whether it completed or failed durably — returns
        ``True``; only an empty queue returns ``False``.
        """
        job = self._queue.dequeue(job_types=self._supported_types)
        if job is None:
            return False

        try:
            self._queue.mark_processing(job.id, generation=job.attempt)
        except (InvalidJobStateError, JobNotFoundError):
            # The claim was fenced away (recovery/re-claim) before we
            # started; another owner is responsible for this row.
            logger.info("job claim is no longer current; skipping", extra={"job_id": job.id})
            return False

        try:
            source = self._resolve_input(job)
            result = self._executor.run(
                {"job_id": job.id, "input_path": str(source)},
                lambda pct: self._emit_progress(job.id, pct),
            )
        except UploadResolutionError as exc:
            self._record_failure(job, exc.error_code)
            return True
        except ExecutorError as exc:
            self._record_failure(job, exc.error_code)
            return True
        except Exception:  # noqa: BLE001 - boundary: map every defect to a code
            # Deliberately no exc_info: tool/frame text may carry local paths.
            logger.error(
                "unhandled worker error",
                extra={"job_id": job.id, "error_type": INTERNAL_ERROR_CODE},
            )
            self._record_failure(job, INTERNAL_ERROR_CODE)
            return True

        try:
            references = self._to_output_references(result["outputs"])
            self._queue.complete_job(
                job.id,
                generation=job.attempt,
                output_paths=references,
            )
        except (InvalidJobStateError, JobNotFoundError):
            logger.info(
                "job completed locally but its claim changed before recording",
                extra={"job_id": job.id},
            )
        except RuntimeError:
            # The executor produced outputs the worker cannot safely publish;
            # never store an unverifiable path. No exc_info to avoid logging it.
            logger.error(
                "output reference mapping failed",
                extra={"job_id": job.id, "error_type": INTERNAL_ERROR_CODE},
            )
            self._record_failure(job, INTERNAL_ERROR_CODE)
        else:
            logger.info(
                "job completed",
                extra={"job_id": job.id, "segment_count": result.get("segment_count")},
            )
        return True

    def run(
        self,
        *,
        poll_interval: float = 1.0,
        max_jobs: int | None = None,
        stop_event: threading.Event | None = None,
    ) -> int:
        """Process jobs until the queue is idle or a limit is reached.

        ``max_jobs`` bounds the number of processed jobs (handy for one-shot
        runs and tests); ``None`` means keep polling until ``stop_event`` is
        set. When the queue is empty and no limit was given, the loop waits
        ``poll_interval`` between claims instead of busy-spinning.
        """
        if poll_interval < 0:
            raise ValueError("poll_interval must be non-negative")
        processed = 0
        while stop_event is None or not stop_event.is_set():
            if max_jobs is not None and processed >= max_jobs:
                break
            if self.process_one():
                processed += 1
                continue
            if max_jobs is not None:
                break
            stop_event.wait(poll_interval)
        return processed

    def _to_output_references(self, outputs: list[str]) -> list[str]:
        """Map absolute executor outputs to storage-root-relative references.

        The queue/API layer must never store or expose absolute filesystem
        paths: the real paths stay inside the executor/worker, while the
        durable record carries POSIX-style references relative to the
        configured output base (e.g. ``<job-id>/part_000.mp4``). A path that
        is not provably inside the base is an internal defect, not a media
        rejection, so the caller maps it to ``INTERNAL_ERROR``.
        """
        if self._output_base is None:
            raise RuntimeError("output base directory is not configured")
        references: list[str] = []
        for raw in outputs:
            resolved = Path(raw).resolve()
            if not resolved.is_relative_to(self._output_base):
                raise RuntimeError("executor output escaped the configured base")
            references.append(resolved.relative_to(self._output_base).as_posix())
        return references

    def _resolve_input(self, job: QueuedJob):
        if job.upload_id is not None:
            if self._upload_store is None:
                raise UploadResolutionError("no upload store is configured")
            return self._upload_store.resolve(job.upload_id)
        if job.input_path is None:
            raise UploadResolutionError("job carries no input reference")
        return Path(job.input_path)

    def _record_failure(self, job: QueuedJob, error_type: str) -> None:
        try:
            self._queue.mark_failed(
                job.id, generation=job.attempt, error_type=error_type
            )
        except (InvalidJobStateError, JobNotFoundError):
            logger.info(
                "failure could not be recorded; claim changed",
                extra={"job_id": job.id, "error_type": error_type},
            )
            return
        logger.warning(
            "job failed", extra={"job_id": job.id, "error_type": error_type}
        )

    def _emit_progress(self, job_id: str, pct: float) -> None:
        if self._progress_sink is not None:
            self._progress_sink(job_id, pct)


def serve(
    queue_factory: QueueFactory,
    executor: FFmpegSplitExecutor,
    *,
    concurrency: int = 1,
    upload_store: LocalUploadStore | None = None,
    output_base_dir: str | Path | None = None,
    supported_types: Sequence[str] = DEFAULT_SUPPORTED_TYPES,
    progress_sink: ProgressSink | None = None,
    poll_interval: float = 1.0,
    max_jobs: int | None = None,
    stop_event: threading.Event | None = None,
) -> int:
    """Run one worker per concurrency slot, each with its own queue connection.

    ``max_jobs`` is one **pool-wide** budget, not a per-thread quota: every
    claim reserves a token from the shared :class:`_JobBudget` first, so the
    total number of processed jobs can never exceed it regardless of thread
    count or scheduling. Returns the total number of processed jobs. The
    shared executor holds no per-job mutable state and the upload store only
    performs read-only resolution, so both are safe to share.
    """
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    if poll_interval < 0:
        raise ValueError("poll_interval must be non-negative")

    stop_event = stop_event or threading.Event()
    budget = _JobBudget(max_jobs)
    totals: list[int] = []
    threads: list[threading.Thread] = []

    def _slot() -> None:
        worker = QueueWorker(
            queue_factory(),
            executor,
            upload_store,
            output_base_dir=output_base_dir,
            supported_types=supported_types,
            progress_sink=progress_sink,
        )
        processed = 0
        try:
            while not stop_event.is_set():
                # Reserve budget before touching the queue so no thread can
                # claim a job the pool cannot afford.
                if not budget.acquire():
                    break
                if worker.process_one():
                    processed += 1
                    continue
                # Empty/stale claim: give the token back and decide whether
                # to wait (unbounded mode) or exit (bounded one-shot mode).
                budget.release()
                if max_jobs is not None:
                    break
                stop_event.wait(poll_interval)
        except Exception:  # noqa: BLE001 - a slot must never die silently
            # Infrastructure failure (e.g. queue unavailable): stop the pool
            # and leave claimed rows to the explicit, evidence-based recovery.
            logger.error("worker slot stopped after an infrastructure error")
            stop_event.set()
        totals.append(processed)

    for _ in range(concurrency):
        thread = threading.Thread(target=_slot, daemon=True)
        threads.append(thread)
        thread.start()
    for thread in threads:
        thread.join()
    return sum(totals)


def build_executor(
    *,
    upload_store_dir: str,
    output_base_dir: str,
    segment_sec: int,
    ffmpeg_cmd: str | None = None,
    ffprobe_cmd: str | None = None,
    timeout_sec: float = 3600.0,
) -> FFmpegSplitExecutor:
    """Construct an executor whose trusted root is the upload store.

    Both opaque-upload jobs and local-path jobs end up referencing files
    inside the same trusted root in the D2 local deployment, so the
    executor's containment check uses the store directory.
    """
    return FFmpegSplitExecutor.from_config(
        allowed_base_dir=upload_store_dir,
        output_base_dir=output_base_dir,
        segment_sec=segment_sec,
        timeout_sec=timeout_sec,
        ffmpeg_cmd=ffmpeg_cmd,
        ffprobe_cmd=ffprobe_cmd,
    )


def configure_logging(level: str = "INFO") -> None:
    """Structured single-line logging without paths or media metadata."""
    logging.basicConfig(
        level=getattr(logging, str(level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
