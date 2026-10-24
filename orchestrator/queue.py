"""Durable SQLite-backed job queue.

Implements the contract documented in ``D2_MVP/04_SQLITE_QUEUE.md``:

* WAL journaling, ``foreign_keys=ON`` and a 5 s ``busy_timeout`` so concurrent
  workers wait for the single writer instead of failing immediately.
* Durable storage in the canonical ``jobs``/``environments``/``job_events``
  tables from ``D1_FOUNDATION/02_DB_SCHEMA.sql`` (the embedded DDL is kept
  structurally identical; ``tests/unit/test_sqlite_queue.py`` guards against
  drift).
* Atomic claim with ``BEGIN IMMEDIATE`` and ``UPDATE ... RETURNING`` so a job
  can never be handed to two workers.
* Ordering by ``priority DESC`` then ``created_at ASC`` (with ``rowid`` as a
  deterministic tie-breaker for rows created inside the same second).

Concurrency model — claim generations (fencing)
-----------------------------------------------
Every claim reads the row's ``retry_count`` and exposes it as
``QueuedJob.attempt``. All worker state transitions must present that
generation; the UPDATE is guarded by ``retry_count = ?``. When a job is
recovered and re-claimed its generation changes, so a stale worker still
holding the old generation can never overwrite the new worker's state.

Crash recovery — evidence only, no time-based guessing
------------------------------------------------------
The D2 SQL example that requeues rows purely because ``started_at`` is old is
deliberately NOT implemented: ``started_at`` alone cannot prove the worker is
dead, and an automatic time-based sweep could requeue a job that is still
running and execute it twice. Recovery is therefore explicit via
:meth:`SQLiteQueue.recover_claimed_jobs`, which requires the caller to supply
the affected job ids together with an audit reason — i.e. the supervisor must
already have reliable evidence that the worker is dead.

The intended evidence source per ``D0_MASTER_BLUEPRINT/ARCHITECTURE.md``
(§4.2/§12.2) is the agent heartbeat (30 s cadence, 90 s timeout marking an
environment offline, recorded in ``environments.last_heartbeat``). That
mechanism is D3 work and does not exist yet; until it is implemented, no
automatic rescheduling runs anywhere in the queue. Each recovery is recorded
in ``job_events`` with the supplied reason.

Each worker/thread should open its own ``SQLiteQueue`` instance; the wrapped
sqlite3 connection is not shared across threads by design.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

# Queue-relevant subset of D1_FOUNDATION/02_DB_SCHEMA.sql.
# Keep these definitions and indexes in sync with that file; the
# schema-consistency test fails if the two definitions drift apart.
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

CREATE TABLE IF NOT EXISTS job_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT REFERENCES jobs(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL,
  payload_json TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_events_job ON job_events(job_id);
CREATE INDEX IF NOT EXISTS idx_events_created ON job_events(created_at);
"""

_STATUS_QUEUED = "queued"
_STATUS_ASSIGNED = "assigned"
_STATUS_PROCESSING = "processing"
_STATUS_COMPLETED = "completed"
_STATUS_FAILED = "failed"
_CLAIMED_STATUSES = (_STATUS_ASSIGNED, _STATUS_PROCESSING)
_DEFAULT_MAX_RETRIES = 3


class QueueError(Exception):
    """Base error for queue operations."""


class JobAlreadyExistsError(QueueError):
    """Raised when enqueue receives an id that is already stored."""


class JobNotFoundError(QueueError):
    """Raised when an operation references an unknown job id."""


class InvalidJobStateError(QueueError):
    """Raised when a state transition is illegal for the current state/generation."""


class InvalidJobInputError(QueueError):
    """Base error for rejected enqueue input files."""


class JobInputNotFoundError(InvalidJobInputError):
    """Raised when the referenced input file does not exist."""


class JobInputPathNotAllowedError(InvalidJobInputError):
    """Raised when the input path escapes the configured trusted directory."""


class JobInputTypeNotAllowedError(InvalidJobInputError):
    """Raised when the input extension is not on the allowed list."""


class JobInputTooLargeError(InvalidJobInputError):
    """Raised when the input file exceeds the configured size limit."""


@dataclass(frozen=True)
class EnqueuePolicy:
    """Static input validation policy sourced from project configuration.

    Build instances from the canonical transfer contract with
    :meth:`from_file_transfer_spec` rather than hard-coding limits.

    ``allowed_base_dir`` is the only trusted root for local input paths. The
    project currently defines no such directory anywhere in its configuration,
    so it defaults to ``None``: in that mode path containment cannot be
    enforced (existence, extension and size still are). The missing setting
    must be added to the configuration before enabling untrusted local paths;
    remote API clients never send filesystem paths at all — they use opaque
    upload ids per ``D2_MVP/02_IMPLEMENTATION_MAP.md``.
    """

    max_file_size_bytes: int
    allowed_extensions: frozenset[str]
    allowed_base_dir: Path | None = None

    @classmethod
    def from_file_transfer_spec(
        cls,
        spec_path: str | Path,
        *,
        allowed_base_dir: str | Path | None = None,
    ) -> EnqueuePolicy:
        """Load limits from ``config/file_transfer_spec.yaml`` ``constraints``."""
        import yaml

        data = yaml.safe_load(Path(spec_path).read_text(encoding="utf-8"))
        constraints = data["constraints"]
        return cls(
            max_file_size_bytes=int(constraints["max_file_size_mb"]) * 1024 * 1024,
            allowed_extensions=frozenset(
                str(ext).lower() for ext in constraints["allowed_extensions"]
            ),
            allowed_base_dir=(
                Path(allowed_base_dir).resolve() if allowed_base_dir else None
            ),
        )


def validate_job_input(path: str | Path, policy: EnqueuePolicy) -> Path:
    """Validate a local input path against the policy before enqueue.

    Performs the static checks available without FFprobe: existence, trusted
    directory containment, allowed extension and size limit. Media-structure
    validation (FFprobe/MIME) remains a separate runtime requirement noted in
    ``config/file_transfer_spec.yaml``.
    """
    candidate = Path(path).expanduser().resolve()
    if not candidate.is_file():
        raise JobInputNotFoundError(f"input file does not exist: {candidate}")

    if policy.allowed_base_dir is not None:
        base = policy.allowed_base_dir.expanduser().resolve()
        if not candidate.is_relative_to(base):
            raise JobInputPathNotAllowedError(
                f"input path {candidate} is outside the allowed directory {base}"
            )

    if candidate.suffix.lower() not in policy.allowed_extensions:
        raise JobInputTypeNotAllowedError(
            f"extension {candidate.suffix!r} is not in "
            f"{sorted(policy.allowed_extensions)}"
        )

    size_bytes = candidate.stat().st_size
    if size_bytes > policy.max_file_size_bytes:
        raise JobInputTooLargeError(
            f"input file is {size_bytes} bytes; limit is "
            f"{policy.max_file_size_bytes} bytes"
        )
    return candidate


@dataclass(frozen=True)
class QueuedJob:
    """A job as exchanged with the queue.

    ``created_at`` may be left empty; the database then stamps
    ``CURRENT_TIMESTAMP`` itself. ``attempt`` is the claim generation
    (``retry_count`` at claim time) and must be echoed back on every worker
    state transition; it is ignored on enqueue.
    """

    id: str
    type: str
    priority: int = 5
    created_at: str = ""
    input_path: str | None = None
    cloud_consent: bool = False
    attempt: int = 0


class SQLiteQueue:
    """Persistent priority queue backed by a single SQLite database file."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        busy_timeout_ms: int = 5000,
        enqueue_policy: EnqueuePolicy | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._busy_timeout_ms = busy_timeout_ms
        self._enqueue_policy = enqueue_policy
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
            attempt=int(row["retry_count"]),
        )

    def enqueue(self, job: QueuedJob) -> None:
        """Insert a job in the ``queued`` state using bound parameters.

        When an :class:`EnqueuePolicy` is configured and the job carries a
        local ``input_path``, the file is validated before any write.
        """
        if not isinstance(job, QueuedJob):
            raise TypeError("enqueue expects a QueuedJob instance")
        if not job.id or not job.type:
            raise ValueError("QueuedJob.id and QueuedJob.type must be non-empty")
        if not 0 <= job.priority <= 10:
            raise ValueError("priority must be between 0 and 10")
        if self._enqueue_policy is not None and job.input_path:
            validate_job_input(job.input_path, self._enqueue_policy)
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
        ``assigned``, carries a ``started_at`` timestamp and its claim
        generation (``retry_count``) is returned as ``QueuedJob.attempt``. The
        transaction commits before any external processing begins.
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
                          cloud_consent, retry_count
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
            SELECT id, type, priority, created_at, input_path, cloud_consent,
                   retry_count
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

    def _transition(
        self,
        job_id: str,
        new_status: str,
        allowed_previous: Sequence[str],
        *,
        generation: int,
        **fields: object,
    ) -> None:
        """Atomically apply a worker state transition with fencing.

        The UPDATE only succeeds when the job still exists, is in one of
        ``allowed_previous`` states and still carries the caller's claim
        generation. A zero-row update means either an unknown id or a stale
        worker / illegal transition, which are reported as distinct errors.
        """
        placeholders = ", ".join("?" for _ in allowed_previous)
        assignments = ", ".join(f"{column} = ?" for column in ("status", *fields))
        values = [new_status, *fields.values()]
        values.extend([job_id, generation, *allowed_previous])
        with self._immediate() as conn:
            cursor = conn.execute(
                f"""
                UPDATE jobs
                SET {assignments}
                WHERE id = ?
                  AND retry_count = ?
                  AND status IN ({placeholders})
                """,
                values,
            )
            if cursor.rowcount == 0:
                current = conn.execute(
                    "SELECT status, retry_count FROM jobs WHERE id = ?",
                    (job_id,),
                ).fetchone()
                if current is None:
                    raise JobNotFoundError(f"unknown job: {job_id}")
                raise InvalidJobStateError(
                    f"job {job_id} cannot transition to {new_status!r}: "
                    f"status={current['status']!r}, generation="
                    f"{current['retry_count']} but caller presented {generation}"
                )

    def mark_processing(self, job_id: str, *, generation: int) -> None:
        """Record that the worker owning ``generation`` started execution."""
        self._transition(
            job_id, _STATUS_PROCESSING, (_STATUS_ASSIGNED,), generation=generation
        )

    def mark_completed(self, job_id: str, *, generation: int) -> None:
        """Record successful completion and stamp ``completed_at``."""
        self._transition(
            job_id,
            _STATUS_COMPLETED,
            _CLAIMED_STATUSES,
            generation=generation,
            completed_at=_sql_timestamp(),
        )

    def mark_failed(
        self, job_id: str, *, generation: int, error_type: str | None = None
    ) -> None:
        """Record a permanent failure without leaking sensitive details."""
        self._transition(
            job_id,
            _STATUS_FAILED,
            _CLAIMED_STATUSES,
            generation=generation,
            error_type=error_type,
        )

    def recover_claimed_jobs(
        self,
        job_ids: Sequence[str],
        *,
        reason: str,
        max_retries: int = _DEFAULT_MAX_RETRIES,
    ) -> int:
        """Requeue or fail claimed jobs after the worker is confirmed dead.

        This is the ONLY supported recovery path and it is evidence-driven:
        the caller (supervisor) passes the explicit ids of jobs whose worker
        has been declared dead plus an audit ``reason``. There is no wall-clock
        sweep, because ``started_at`` cannot distinguish a slow-but-alive
        worker from a dead one. Heartbeat-based liveness is the intended future
        evidence source (D3); until then callers must not invent evidence.

        Jobs still within the retry budget return to ``queued`` with
        ``retry_count`` incremented and ``started_at`` cleared, making them
        claimable again under a new generation; jobs at/over ``max_retries``
        become ``failed``. Every decision is appended to ``job_events``. Jobs
        that are not currently claimed are left untouched. Returns the number
        of recovered rows.
        """
        ids = tuple(job_ids)
        if not ids:
            raise ValueError("job_ids must not be empty")
        if not reason or not reason.strip():
            raise ValueError("an explicit, non-empty recovery reason is required")

        with self._immediate() as conn:
            found = {
                row["id"]
                for row in conn.execute(
                    f"SELECT id FROM jobs WHERE id IN ({', '.join('?' for _ in ids)})",
                    ids,
                ).fetchall()
            }
            missing = sorted(set(ids) - found)
            if missing:
                raise JobNotFoundError(f"unknown jobs: {', '.join(missing)}")

            claimed = conn.execute(
                f"""
                SELECT id, status, retry_count
                FROM jobs
                WHERE id IN ({', '.join('?' for _ in ids)})
                  AND status IN (?, ?)
                """,
                (*ids, _STATUS_ASSIGNED, _STATUS_PROCESSING),
            ).fetchall()
            if not claimed:
                return 0

            claimed_ids = tuple(row["id"] for row in claimed)
            cursor = conn.execute(
                f"""
                UPDATE jobs
                SET status = CASE
                        WHEN retry_count >= ? THEN 'failed'
                        ELSE 'queued'
                    END,
                    retry_count = retry_count + 1,
                    started_at = NULL
                WHERE id IN ({', '.join('?' for _ in claimed_ids)})
                """,
                (max_retries, *claimed_ids),
            )

            for row in claimed:
                will_fail = row["retry_count"] >= max_retries
                conn.execute(
                    """
                    INSERT INTO job_events (job_id, event_type, payload_json)
                    VALUES (?, ?, ?)
                    """,
                    (
                        row["id"],
                        "job_failed" if will_fail else "job_requeued",
                        json.dumps(
                            {
                                "reason": reason,
                                "previous_status": row["status"],
                                "attempt": row["retry_count"],
                            }
                        ),
                    ),
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
