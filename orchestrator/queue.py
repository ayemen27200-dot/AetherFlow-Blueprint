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

Input validation — fail closed
------------------------------
Local filesystem paths (``QueuedJob.input_path``, the trusted local CLI flow)
are accepted only when the queue holds an :class:`EnqueuePolicy` whose
``allowed_base_dir`` is set explicitly; the symlink-resolved real file must
live inside that root and pass the extension/size limits loaded from
``config/file_transfer_spec.yaml``. With no policy, or no trusted root, every
local path is rejected before any database write. Remote API jobs never send
paths: they carry an opaque ``QueuedJob.upload_id`` (the ``POST /uploads``
contract in ``D2_MVP/02_IMPLEMENTATION_MAP.md``) which is token-validated and
never passed to filesystem APIs.

Each worker/thread should open its own ``SQLiteQueue`` instance; the wrapped
sqlite3 connection is not shared across threads by design.
"""

from __future__ import annotations

import json
import re
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

# Internal storage convention for opaque remote upload ids. The canonical D1
# schema has only an ``input_path`` column, so an upload id is persisted there
# with this prefix and never handed to filesystem APIs.
_UPLOAD_PREFIX = "upload://"
# Opaque server-issued ids are bounded tokens with no path semantics: no
# separators, no parent references, no NUL characters. The OpenAPI contract
# types ``upload_id`` as a plain string without a documented issuer pattern
# (POST /uploads is not implemented yet), so this is a conservative queue-side
# token bound rather than a guess at the future issuer format.
_UPLOAD_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


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


class JobInputPolicyNotConfiguredError(InvalidJobInputError):
    """Raised when a local path is enqueued without a validation policy.

    Fail-closed guard: local filesystem paths are never accepted on the
    assumption that validation is unnecessary.
    """


class JobInputTrustedRootNotConfiguredError(InvalidJobInputError):
    """Raised when the policy cannot identify a trusted root directory.

    Without an explicit ``allowed_base_dir`` path containment cannot be
    proven, so local paths are rejected rather than partially validated.
    """


class InvalidUploadIdError(InvalidJobInputError):
    """Raised when an opaque upload id has path-like or malformed content."""


@dataclass(frozen=True)
class EnqueuePolicy:
    """Static input validation policy sourced from project configuration.

    Build instances from the canonical transfer contract with
    :meth:`from_file_transfer_spec` rather than hard-coding limits.

    ``allowed_base_dir`` is the only trusted root for local input paths. The
    project currently defines no such directory anywhere in its configuration,
    so it defaults to ``None`` and the queue then **rejects every local
    ``input_path``** (fail closed): partial validation without proven
    containment is not accepted. A trusted root must be configured explicitly
    (deployment configuration, not a hard-coded guess) before local CLI paths
    can be enqueued. Remote API clients never send filesystem paths — they use
    opaque upload ids per ``D2_MVP/02_IMPLEMENTATION_MAP.md``, which take the
    separate :attr:`QueuedJob.upload_id` channel and need no policy.
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

    Fails closed when the policy has no explicit trusted root. Otherwise
    performs the static checks available without FFprobe, all against the
    symlink-resolved real path: existence, trusted-directory containment,
    allowed extension and size limit. Media-structure validation
    (FFprobe/MIME) remains a separate runtime requirement noted in
    ``config/file_transfer_spec.yaml``.
    """
    if policy.allowed_base_dir is None:
        raise JobInputTrustedRootNotConfiguredError(
            "an explicit allowed_base_dir is required before local input "
            "paths can be validated; configure a trusted input directory"
        )

    # resolve() follows symlinks, so a link placed inside the trusted root but
    # pointing outside it is evaluated against its real target location.
    candidate = Path(path).expanduser().resolve()
    base = policy.allowed_base_dir.expanduser().resolve()
    if not candidate.is_relative_to(base):
        raise JobInputPathNotAllowedError(
            f"input path {candidate} is outside the allowed directory {base}"
        )

    if not candidate.is_file():
        raise JobInputNotFoundError(f"input file does not exist: {candidate}")

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


def validate_upload_id(upload_id: str) -> str:
    """Validate an opaque remote upload id without touching the filesystem.

    Upload ids are server-issued tokens (see ``POST /uploads`` in the OpenAPI
    contract), never paths. Path separators, parent references and control
    characters are rejected so an id can never be reinterpreted as a local
    filesystem location. The filesystem special names ``.`` and ``..`` — and
    every dot-only string — are rejected explicitly even though the charset
    regex would otherwise admit them.

    This is a syntax/shape check only. It does not prove that the id exists or
    that the caller owns it; the future upload-store integration must verify
    existence/ownership against ``POST /uploads`` state instead of deriving a
    filesystem path from the id.
    """
    if not isinstance(upload_id, str) or not _UPLOAD_ID_RE.fullmatch(upload_id):
        raise InvalidUploadIdError(
            "upload id must be 1-128 characters from [A-Za-z0-9._-] with no "
            "path separators or parent references"
        )
    if upload_id.strip(".") == "":
        raise InvalidUploadIdError(
            "upload id must not be '.' or '..' or consist solely of dots; "
            "filesystem special names are not valid opaque upload ids"
        )
    return upload_id


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
    upload_id: str | None = None
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
        stored = row["input_path"]
        if isinstance(stored, str) and stored.startswith(_UPLOAD_PREFIX):
            return QueuedJob(
                id=row["id"],
                type=row["type"],
                priority=row["priority"],
                created_at=row["created_at"] or "",
                input_path=None,
                upload_id=stored[len(_UPLOAD_PREFIX) :],
                cloud_consent=bool(row["cloud_consent"]),
                attempt=int(row["retry_count"]),
            )
        return QueuedJob(
            id=row["id"],
            type=row["type"],
            priority=row["priority"],
            created_at=row["created_at"] or "",
            input_path=stored,
            upload_id=None,
            cloud_consent=bool(row["cloud_consent"]),
            attempt=int(row["retry_count"]),
        )

    def enqueue(self, job: QueuedJob) -> None:
        """Insert a job in the ``queued`` state using bound parameters.

        Input handling is fail-closed and split into two explicit channels:

        * Local ``input_path`` values require a configured
          :class:`EnqueuePolicy` with an explicit trusted root; the resolved
          real file is fully validated *before any SQLite write*. A missing
          policy or trusted root raises instead of silently accepting the
          path.
        * Opaque ``upload_id`` values (remote API flow per
          ``D2_MVP/02_IMPLEMENTATION_MAP.md``) are token-validated only and
          never touch the filesystem.

        The two channels are mutually exclusive.
        """
        if not isinstance(job, QueuedJob):
            raise TypeError("enqueue expects a QueuedJob instance")
        if not job.id or not job.type:
            raise ValueError("QueuedJob.id and QueuedJob.type must be non-empty")
        if not 0 <= job.priority <= 10:
            raise ValueError("priority must be between 0 and 10")
        if job.input_path and job.upload_id:
            raise ValueError(
                "QueuedJob.input_path and upload_id are mutually exclusive"
            )

        stored_input: str | None
        if job.upload_id is not None:
            validate_upload_id(job.upload_id)
            stored_input = _UPLOAD_PREFIX + job.upload_id
        elif job.input_path:
            # Fail closed: no policy, or a policy without a proven trusted
            # root, means local paths cannot be accepted at all.
            if self._enqueue_policy is None:
                raise JobInputPolicyNotConfiguredError(
                    "a local input_path was supplied but the queue has no "
                    "EnqueuePolicy; configure a policy with an explicit "
                    "allowed_base_dir or use an opaque upload_id instead"
                )
            validated = validate_job_input(job.input_path, self._enqueue_policy)
            stored_input = str(validated)
        else:
            stored_input = None

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
                        stored_input,
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

        This is the ONLY supported recovery path and it is evidence-driven by
        the caller, not by this method. The queue has no heartbeat/liveness
        source yet (that is D3 work feeding ``environments.last_heartbeat``),
        so it cannot decide by itself that a worker died:

        * ``job_ids`` must name jobs whose worker a *trusted supervisor* has
          already declared dead through external evidence.
        * ``reason`` is a free-text **audit annotation**, recorded verbatim
          into ``job_events``; it is never parsed or verified as liveness
          evidence, and supplying a string does not by itself justify
          recovery.
        * Nothing in this package calls this method automatically — there is
          no wall-clock sweep, because ``started_at`` cannot distinguish a
          slow-but-alive worker from a dead one. Invocation is solely the
          supervisor's responsibility after confirmed worker death.

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
