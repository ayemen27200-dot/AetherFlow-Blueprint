"""Durable SQLite-backed job queue.

Implements the contract documented in ``D2_MVP/04_SQLITE_QUEUE.md``:

* WAL journaling, ``foreign_keys=ON`` and a 5 s ``busy_timeout`` so concurrent
  workers wait for the single writer instead of failing immediately.
* Durable storage in the canonical ``jobs`` table from
  ``D1_FOUNDATION/02_DB_SCHEMA.sql`` (the embedded DDL is kept structurally
  identical; ``tests/unit/test_sqlite_queue.py`` guards against drift).
* Atomic claim with ``BEGIN IMMEDIATE`` and ``UPDATE ... RETURNING`` so a job
  can never be handed to two workers.
* Ordering by ``priority DESC`` then ``created_at ASC`` (with ``rowid`` as a
  deterministic tie-breaker for rows created inside the same second).
* Crash recovery through :meth:`SQLiteQueue.recover_stalled`, matching the
  retry policy in the D2 queue design.

Each worker/thread should open its own ``SQLiteQueue`` instance; the wrapped
sqlite3 connection is not shared across threads by design.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

# Queue-relevant subset of D1_FOUNDATION/02_DB_SCHEMA.sql.
# Keep the jobs/environments definitions and indexes in sync with that file;
# the schema-consistency test fails if the two definitions drift apart.
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS environments (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  source TEXT CHECK(source IN ('agent','static')),
  capabilities_json TEXT,
  last_heartbeat TIMESTAMP,
  status TEXT CHECK(status IN ('active','offline','maintenance'))
);

CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  priority INTEGER DEFAULT 5 CHECK(priority BETWEEN 0 AND 10),
  status TEXT NOT NULL,
  environment TEXT REFERENCES environments(id),
  input_path TEXT,
  output_paths TEXT,
  error_type TEXT,
  retry_count INTEGER DEFAULT 0,
  checkpoint_data TEXT,
  cloud_consent INTEGER DEFAULT 0 CHECK(cloud_consent IN (0,1)),
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  started_at TIMESTAMP,
  completed_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_priority ON jobs(priority DESC, created_at ASC);
"""

_STATUS_QUEUED = "queued"
_STATUS_ASSIGNED = "assigned"
_STATUS_PROCESSING = "processing"
_STATUS_COMPLETED = "completed"
_STATUS_FAILED = "failed"


class QueueError(Exception):
    """Base error for queue operations."""


class JobAlreadyExistsError(QueueError):
    """Raised when enqueue receives an id that is already stored."""


class JobNotFoundError(QueueError):
    """Raised when a state transition references an unknown job id."""


@dataclass(frozen=True)
class QueuedJob:
    """A job as exchanged with the queue.

    ``created_at`` may be left empty; the database then stamps
    ``CURRENT_TIMESTAMP`` itself.
    """

    id: str
    type: str
    priority: int = 5
    created_at: str = ""
    input_path: str | None = None
    cloud_consent: bool = False


class SQLiteQueue:
    """Persistent priority queue backed by a single SQLite database file."""

    def __init__(self, db_path: str | Path, *, busy_timeout_ms: int = 5000) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._busy_timeout_ms = busy_timeout_ms
        # ``timeout`` mirrors busy_timeout so the low-level driver waits too.
        self._conn = sqlite3.connect(
            str(self.db_path),
            timeout=busy_timeout_ms / 1000,
        )
        self._conn.row_factory = sqlite3.Row
        self._configure_connection(self._conn, busy_timeout_ms)
        self._create_schema()

    @staticmethod
    def _configure_connection(conn: sqlite3.Connection, busy_timeout_ms: int) -> None:
        # journal_mode is persisted on the database file; the other pragmas
        # are per-connection and must be reset for every new connection.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")

    def _create_schema(self) -> None:
        self._conn.executescript(_SCHEMA_SQL)

    @contextmanager
    def _immediate(self) -> Iterator[sqlite3.Connection]:
        """Hold a reserved write lock for the duration of one transaction."""
        conn = self._conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except Exception:
            conn.rollback()
            raise
        else:
            conn.commit()

    @staticmethod
    def _to_job(row: sqlite3.Row) -> QueuedJob:
        return QueuedJob(
            id=row["id"],
            type=row["type"],
            priority=row["priority"],
            created_at=row["created_at"] or "",
            input_path=row["input_path"],
            cloud_consent=bool(row["cloud_consent"]),
        )

    def enqueue(self, job: QueuedJob) -> None:
        """Insert a job in the ``queued`` state using bound parameters."""
        if not isinstance(job, QueuedJob):
            raise TypeError("enqueue expects a QueuedJob instance")
        if not job.id or not job.type:
            raise ValueError("QueuedJob.id and QueuedJob.type must be non-empty")
        if not 0 <= job.priority <= 10:
            raise ValueError("priority must be between 0 and 10")
        try:
            with self._immediate() as conn:
                conn.execute(
                    """
                    INSERT INTO jobs
                        (id, type, priority, status, input_path,
                         cloud_consent, created_at)
                    VALUES (?, ?, ?, 'queued', ?, ?,
                            COALESCE(NULLIF(?, ''), CURRENT_TIMESTAMP))
                    """,
                    (
                        job.id,
                        job.type,
                        job.priority,
                        job.input_path,
                        int(job.cloud_consent),
                        job.created_at,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise JobAlreadyExistsError(f"job already exists: {job.id}") from exc

    def dequeue(self) -> QueuedJob | None:
        """Atomically claim the highest-priority queued job.

        Returns ``None`` when the queue is empty. The claimed job moves to
        ``assigned`` and carries a ``started_at`` timestamp; the transaction
        commits before any external processing begins.
        """
        with self._immediate() as conn:
            row = conn.execute(
                """
                UPDATE jobs
                SET status = ?,
                    started_at = CURRENT_TIMESTAMP
                WHERE id = (
                    SELECT id
                    FROM jobs
                    WHERE status = ?
                    ORDER BY priority DESC, created_at ASC, rowid ASC
                    LIMIT 1
                )
                RETURNING id, type, priority, created_at, input_path,
                          cloud_consent
                """,
                (_STATUS_ASSIGNED, _STATUS_QUEUED),
            ).fetchone()
        if row is None:
            return None
        return self._to_job(row)

    def size(self) -> int:
        """Number of jobs still waiting to be claimed (status ``queued``)."""
        return self.count(_STATUS_QUEUED)

    def count(self, status: str | None = None) -> int:
        """Count jobs, optionally restricted to one status."""
        conn = self._conn
        if status is None:
            row = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE status = ?", (status,)
            ).fetchone()
        return int(row[0])

    def get(self, job_id: str) -> QueuedJob | None:
        """Return the stored job or ``None`` if the id is unknown."""
        row = self._conn.execute(
            """
            SELECT id, type, priority, created_at, input_path, cloud_consent
            FROM jobs WHERE id = ?
            """,
            (job_id,),
        ).fetchone()
        return None if row is None else self._to_job(row)

    def get_status(self, job_id: str) -> str | None:
        """Return the current status string or ``None`` for unknown ids."""
        row = self._conn.execute(
            "SELECT status FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return None if row is None else str(row[0])

    def _transition(self, job_id: str, status: str, **fields: object) -> None:
        assignments = ", ".join(f"{column} = ?" for column in ("status", *fields))
        values = [status, *fields.values(), job_id]
        with self._immediate() as conn:
            cursor = conn.execute(
                f"UPDATE jobs SET {assignments} WHERE id = ?", values
            )
            if cursor.rowcount == 0:
                raise JobNotFoundError(f"unknown job: {job_id}")

    def mark_processing(self, job_id: str) -> None:
        """Record that a worker has started executing a claimed job."""
        self._transition(job_id, _STATUS_PROCESSING)

    def mark_completed(self, job_id: str) -> None:
        """Record successful completion and stamp ``completed_at``."""
        self._transition(job_id, _STATUS_COMPLETED, completed_at=_sql_timestamp())

    def mark_failed(self, job_id: str, error_type: str | None = None) -> None:
        """Record a permanent failure without leaking sensitive details."""
        self._transition(job_id, _STATUS_FAILED, error_type=error_type)

    def recover_stalled(
        self,
        *,
        stall_seconds: int = 90,
        max_retries: int = 3,
        statuses: tuple[str, ...] = (_STATUS_PROCESSING,),
    ) -> int:
        """Requeue or fail jobs whose worker stopped reporting progress.

        Mirrors the policy in ``D2_MVP/04_SQLITE_QUEUE.md``: jobs in one of
        ``statuses`` whose ``started_at`` is older than ``stall_seconds`` are
        requeued (``retry_count`` incremented) until they reach
        ``max_retries``, after which they become ``failed``. Returns the
        number of recovered rows.
        """
        if stall_seconds < 0:
            raise ValueError("stall_seconds must be non-negative")
        placeholders = ", ".join("?" for _ in statuses)
        with self._immediate() as conn:
            cursor = conn.execute(
                f"""
                UPDATE jobs
                SET status = CASE
                        WHEN retry_count >= ? THEN 'failed'
                        ELSE 'queued'
                    END,
                    retry_count = retry_count + 1,
                    started_at = NULL
                WHERE status IN ({placeholders})
                  AND started_at IS NOT NULL
                  AND started_at < datetime('now', ?)
                """,
                (max_retries, *statuses, f"-{stall_seconds} seconds"),
            )
            return cursor.rowcount

    def close(self) -> None:
        """Close the underlying database connection."""
        self._conn.close()

    def __enter__(self) -> SQLiteQueue:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()


def _sql_timestamp() -> str:
    """Current UTC timestamp in SQLite's ``YYYY-MM-DD HH:MM:SS`` format."""
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
