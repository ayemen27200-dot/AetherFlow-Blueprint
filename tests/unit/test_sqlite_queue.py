"""Unit tests for the durable SQLite queue (D2 contract).

Every test uses an isolated temporary database; no production data, network
access or paid resources are involved.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from orchestrator.queue import (
    JobAlreadyExistsError,
    JobNotFoundError,
    QueuedJob,
    SQLiteQueue,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_SCHEMA = REPO_ROOT / "D1_FOUNDATION" / "02_DB_SCHEMA.sql"


def make_job(
    job_id: str,
    *,
    priority: int = 5,
    job_type: str = "split",
    created_at: str = "",
) -> QueuedJob:
    return QueuedJob(
        id=job_id, type=job_type, priority=priority, created_at=created_at
    )


def raw_connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


# --------------------------------------------------------------------------- #
# Basic enqueue / dequeue behaviour
# --------------------------------------------------------------------------- #


def test_enqueue_makes_job_visible_as_queued(tmp_path: Path) -> None:
    with SQLiteQueue(tmp_path / "q.db") as queue:
        queue.enqueue(make_job("j1"))

        assert queue.size() == 1
        assert queue.count() == 1
        assert queue.get_status("j1") == "queued"
        stored = queue.get("j1")
        assert stored is not None
        assert stored.id == "j1"
        # created_at is stamped by the database when not supplied.
        assert stored.created_at != ""


def test_dequeue_on_empty_queue_returns_none(tmp_path: Path) -> None:
    with SQLiteQueue(tmp_path / "q.db") as queue:
        assert queue.dequeue() is None
        assert queue.size() == 0


def test_dequeue_claims_atomically_and_marks_assigned(tmp_path: Path) -> None:
    db_path = tmp_path / "q.db"
    with SQLiteQueue(db_path) as queue:
        queue.enqueue(make_job("j1"))
        claimed = queue.dequeue()

        assert claimed is not None
        assert claimed.id == "j1"
        assert queue.size() == 0
        assert queue.get_status("j1") == "assigned"
        # No queued job remains, so the next claim is empty.
        assert queue.dequeue() is None

    with raw_connect(db_path) as conn:
        row = conn.execute("SELECT started_at FROM jobs WHERE id = 'j1'").fetchone()
        assert row["started_at"] is not None


def test_ordering_is_priority_then_creation_time_then_insertion(tmp_path: Path) -> None:
    with SQLiteQueue(tmp_path / "q.db") as queue:
        queue.enqueue(make_job("low_old", priority=1, created_at="2026-01-01 00:00:00"))
        queue.enqueue(make_job("high_new", priority=9, created_at="2026-01-03 00:00:00"))
        queue.enqueue(make_job("high_old", priority=9, created_at="2026-01-02 00:00:00"))
        # Same priority and (in practice) equal created_at: insertion order
        # must win through the rowid tie-breaker.
        queue.enqueue(make_job("tie_a", priority=5))
        queue.enqueue(make_job("tie_b", priority=5))

        order = []
        while True:
            job = queue.dequeue()
            if job is None:
                break
            order.append(job.id)

    assert order == ["high_old", "high_new", "tie_a", "tie_b", "low_old"]


# --------------------------------------------------------------------------- #
# Durability and configuration
# --------------------------------------------------------------------------- #


def test_reopening_database_preserves_queue_state(tmp_path: Path) -> None:
    db_path = tmp_path / "q.db"
    with SQLiteQueue(db_path) as queue:
        queue.enqueue(make_job("j1"))
        queue.enqueue(make_job("j2"))
        assert queue.dequeue() is not None  # one job assigned, still pending

    # A brand-new queue instance (simulated service restart) sees the state.
    with SQLiteQueue(db_path) as restarted:
        assert restarted.count() == 2
        assert restarted.size() == 1
        assert restarted.get_status("j1") == "assigned"
        next_job = restarted.dequeue()
        assert next_job is not None
        assert next_job.id == "j2"


def test_connection_pragmas_are_applied(tmp_path: Path) -> None:
    with SQLiteQueue(tmp_path / "q.db") as queue:
        conn = queue._conn  # noqa: SLF001 - white-box pragma check
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert (tmp_path / "q.db-wal").exists() or (tmp_path / "q.db").exists()


# --------------------------------------------------------------------------- #
# Validation and error handling
# --------------------------------------------------------------------------- #


def test_duplicate_enqueue_is_rejected(tmp_path: Path) -> None:
    with SQLiteQueue(tmp_path / "q.db") as queue:
        queue.enqueue(make_job("j1"))
        with pytest.raises(JobAlreadyExistsError):
            queue.enqueue(make_job("j1"))
        # The failed transaction must not poison subsequent operations.
        assert queue.size() == 1


def test_enqueue_validates_arguments(tmp_path: Path) -> None:
    with SQLiteQueue(tmp_path / "q.db") as queue:
        with pytest.raises(ValueError, match="non-empty"):
            queue.enqueue(QueuedJob(id="", type="split"))
        with pytest.raises(ValueError, match="non-empty"):
            queue.enqueue(QueuedJob(id="j", type=""))
        with pytest.raises(ValueError, match="priority"):
            queue.enqueue(make_job("j", priority=11))
        with pytest.raises(TypeError):
            queue.enqueue("not-a-job")  # type: ignore[arg-type]]


def test_database_check_constraint_rejects_out_of_range_priority(tmp_path: Path) -> None:
    db_path = tmp_path / "q.db"
    SQLiteQueue(db_path).close()
    # Bypass the application guard to prove the schema CHECK still protects.
    with raw_connect(db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO jobs (id, type, priority, status) VALUES (?, ?, ?, 'queued')",
                ("bad", "split", 99),
            )


def test_lifecycle_transitions_and_unknown_job_errors(tmp_path: Path) -> None:
    with SQLiteQueue(tmp_path / "q.db") as queue:
        queue.enqueue(make_job("j1"))
        job = queue.dequeue()
        assert job is not None

        queue.mark_processing("j1")
        assert queue.get_status("j1") == "processing"
        queue.mark_failed("j1", error_type="TOOL_ERROR")
        assert queue.get_status("j1") == "failed"

        queue.enqueue(make_job("j2"))
        queue.dequeue()
        queue.mark_completed("j2")
        assert queue.get_status("j2") == "completed"

        with pytest.raises(JobNotFoundError):
            queue.mark_completed("missing")

    with raw_connect(tmp_path / "q.db") as conn:
        failed = conn.execute(
            "SELECT error_type, completed_at FROM jobs WHERE id = 'j1'"
        ).fetchone()
        completed = conn.execute(
            "SELECT completed_at FROM jobs WHERE id = 'j2'"
        ).fetchone()
        assert failed["error_type"] == "TOOL_ERROR"
        assert completed["completed_at"] is not None


# --------------------------------------------------------------------------- #
# Crash recovery
# --------------------------------------------------------------------------- #


def test_recover_stalled_requeues_and_then_fails(tmp_path: Path) -> None:
    db_path = tmp_path / "q.db"
    with SQLiteQueue(db_path) as queue:
        for job_id in ("j_retry", "j_old_retry", "j_recent", "j_exhausted"):
            queue.enqueue(make_job(job_id))
            queue.dequeue()
            queue.mark_processing(job_id)

        with raw_connect(db_path) as conn:
            # Three jobs have been stalled for more than 90 seconds; one is fresh.
            conn.execute(
                """
                UPDATE jobs
                SET started_at = datetime('now', '-120 seconds')
                WHERE id IN ('j_retry', 'j_old_retry', 'j_exhausted')
                """
            )
            # One job already exhausted its retry budget.
            conn.execute(
                "UPDATE jobs SET retry_count = 3 WHERE id = 'j_exhausted'"
            )
            conn.commit()

        recovered = queue.recover_stalled()
        assert recovered == 3

        assert queue.get_status("j_retry") == "queued"
        assert queue.get_status("j_old_retry") == "queued"
        assert queue.get_status("j_recent") == "processing"
        assert queue.get_status("j_exhausted") == "failed"

        with raw_connect(db_path) as conn:
            retries = {
                row["id"]: row["retry_count"]
                for row in conn.execute(
                    "SELECT id, retry_count FROM jobs"
                ).fetchall()
            }
        assert retries["j_retry"] == 1
        assert retries["j_exhausted"] == 4

        # Requeued jobs are claimable again and keep their identity.
        claimed = queue.dequeue()
        assert claimed is not None
        assert claimed.id in {"j_retry", "j_old_retry"}


def test_recover_stalled_validates_arguments(tmp_path: Path) -> None:
    with SQLiteQueue(tmp_path / "q.db") as queue:
        with pytest.raises(ValueError, match="non-negative"):
            queue.recover_stalled(stall_seconds=-1)


# --------------------------------------------------------------------------- #
# Concurrency and locking
# --------------------------------------------------------------------------- #


def test_concurrent_workers_claim_each_job_exactly_once(tmp_path: Path) -> None:
    db_path = tmp_path / "q.db"
    job_ids = [f"job-{i:03d}" for i in range(60)]
    with SQLiteQueue(db_path) as producer:
        for index, job_id in enumerate(job_ids):
            producer.enqueue(make_job(job_id, priority=index % 11))

    claimed: list[str] = []
    claim_lock = threading.Lock()
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            with SQLiteQueue(db_path) as worker_queue:
                while True:
                    job = worker_queue.dequeue()
                    if job is None:
                        return
                    with claim_lock:
                        claimed.append(job.id)
        except BaseException as exc:  # pragma: no cover - surfaced via assertion
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    assert len(claimed) == len(job_ids)
    assert set(claimed) == set(job_ids)
    with SQLiteQueue(db_path) as queue:
        assert queue.size() == 0
        assert queue.count("assigned") == len(job_ids)


def test_busy_timeout_raises_when_write_lock_is_held(tmp_path: Path) -> None:
    db_path = tmp_path / "q.db"
    queue = SQLiteQueue(db_path, busy_timeout_ms=200)
    holder = raw_connect(db_path)
    holder.execute("BEGIN IMMEDIATE")
    try:
        # A real write must wait for the held reserved lock and then fail
        # once the short busy timeout elapses.
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            queue.enqueue(make_job("blocked"))
    finally:
        holder.rollback()
        holder.close()

    # After the lock is released, normal operations work again, and the
    # failed transaction left no partial state behind.
    queue.enqueue(make_job("j1"))
    assert queue.size() == 1
    queue.close()


# --------------------------------------------------------------------------- #
# Schema consistency with the canonical D1 definition
# --------------------------------------------------------------------------- #


def test_embedded_schema_matches_canonical_definition(tmp_path: Path) -> None:
    assert CANONICAL_SCHEMA.is_file()

    canonical = sqlite3.connect(":memory:")
    canonical.executescript(CANONICAL_SCHEMA.read_text(encoding="utf-8"))

    with SQLiteQueue(tmp_path / "q.db") as queue:
        embedded = queue._conn  # noqa: SLF001 - white-box schema comparison

        canonical_cols = canonical.execute("PRAGMA table_info(jobs)").fetchall()
        embedded_cols = embedded.execute("PRAGMA table_info(jobs)").fetchall()
        assert [tuple(row) for row in embedded_cols] == [
            tuple(row) for row in canonical_cols
        ]

        canonical_indexes = canonical.execute(
            """
            SELECT name, sql FROM sqlite_master
            WHERE type = 'index' AND tbl_name = 'jobs'
            ORDER BY name
            """
        ).fetchall()
        embedded_indexes = embedded.execute(
            """
            SELECT name, sql FROM sqlite_master
            WHERE type = 'index' AND tbl_name = 'jobs'
            ORDER BY name
            """
        ).fetchall()
        assert [tuple(row) for row in embedded_indexes] == [
            tuple(row) for row in canonical_indexes
        ]

        # The jobs table foreign key needs its referenced parent table.
        assert embedded.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='environments'"
        ).fetchone() is not None
    canonical.close()
