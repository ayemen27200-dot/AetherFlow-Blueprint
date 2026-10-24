"""Unit tests for the durable SQLite queue (D2 contract).

Every test uses an isolated temporary database and synthetic temporary files;
no production data, network access or paid resources are involved.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest

from orchestrator.queue import (
    EnqueuePolicy,
    InvalidJobInputError,
    InvalidJobStateError,
    InvalidUploadIdError,
    JobAlreadyExistsError,
    JobInputNotFoundError,
    JobInputPathNotAllowedError,
    JobInputPolicyNotConfiguredError,
    JobInputTooLargeError,
    JobInputTrustedRootNotConfiguredError,
    JobInputTypeNotAllowedError,
    JobNotFoundError,
    QueuedJob,
    SQLiteQueue,
    validate_job_input,
    validate_upload_id,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_SCHEMA = REPO_ROOT / "D1_FOUNDATION" / "02_DB_SCHEMA.sql"
TRANSFER_SPEC = REPO_ROOT / "config" / "file_transfer_spec.yaml"


def make_job(
    job_id: str,
    *,
    priority: int = 5,
    job_type: str = "split",
    created_at: str = "",
    input_path: str | None = None,
) -> QueuedJob:
    return QueuedJob(
        id=job_id,
        type=job_type,
        priority=priority,
        created_at=created_at,
        input_path=input_path,
    )


def raw_connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def make_policy(base_dir: Path, *, max_bytes: int = 1024) -> EnqueuePolicy:
    return EnqueuePolicy(
        max_file_size_bytes=max_bytes,
        allowed_extensions=frozenset({".mp4", ".wav"}),
        allowed_base_dir=base_dir,
    )


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
        assert stored.attempt == 0
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
        assert claimed.attempt == 0
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
            queue.enqueue("not-a-job")  # type: ignore[arg-type]


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


# --------------------------------------------------------------------------- #
# Enqueue input policy (file/path/size validation)
# --------------------------------------------------------------------------- #


def test_policy_loads_project_limits_from_transfer_spec() -> None:
    policy = EnqueuePolicy.from_file_transfer_spec(TRANSFER_SPEC)
    assert policy.max_file_size_bytes == 2000 * 1024 * 1024
    assert policy.allowed_extensions == frozenset(
        {".mp4", ".mkv", ".mov", ".mp3", ".wav", ".m4a"}
    )
    # The project defines no trusted input directory yet; with base dir None
    # the queue fails closed and rejects every local path until an explicit
    # trusted root is supplied to from_file_transfer_spec().
    assert policy.allowed_base_dir is None


def test_enqueue_accepts_valid_file_inside_trusted_dir(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    media = incoming / "clip.mp4"
    media.write_bytes(b"synthetic-bytes")
    policy = make_policy(incoming)

    with SQLiteQueue(tmp_path / "q.db", enqueue_policy=policy) as queue:
        queue.enqueue(make_job("j1", input_path=str(media)))
        assert queue.get_status("j1") == "queued"


def test_enqueue_rejects_missing_file(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    policy = make_policy(incoming)

    with SQLiteQueue(tmp_path / "q.db", enqueue_policy=policy) as queue:
        missing = incoming / "ghost.mp4"
        with pytest.raises(JobInputNotFoundError):
            queue.enqueue(make_job("j1", input_path=str(missing)))
        assert queue.size() == 0


def test_enqueue_rejects_path_outside_trusted_dir(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    outside_dir = tmp_path / "elsewhere"
    outside_dir.mkdir()
    outside = outside_dir / "clip.mp4"
    outside.write_bytes(b"x")
    policy = make_policy(incoming)

    with SQLiteQueue(tmp_path / "q.db", enqueue_policy=policy) as queue:
        with pytest.raises(JobInputPathNotAllowedError):
            queue.enqueue(make_job("j1", input_path=str(outside)))

        # Path traversal must resolve and be rejected identically.
        traversal = incoming / "subdir" / ".." / ".." / "elsewhere" / "clip.mp4"
        with pytest.raises(JobInputPathNotAllowedError):
            validate_job_input(traversal, policy)
        assert queue.size() == 0


def test_enqueue_rejects_disallowed_extension(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    document = incoming / "notes.txt"
    document.write_text("not media")
    policy = make_policy(incoming)

    with SQLiteQueue(tmp_path / "q.db", enqueue_policy=policy) as queue:
        with pytest.raises(JobInputTypeNotAllowedError):
            queue.enqueue(make_job("j1", input_path=str(document)))
        assert queue.size() == 0


def test_enqueue_rejects_oversized_file_before_insert(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    big = incoming / "big.mp4"
    big.write_bytes(b"x" * 50)
    policy = make_policy(incoming, max_bytes=20)

    with SQLiteQueue(tmp_path / "q.db", enqueue_policy=policy) as queue:
        with pytest.raises(JobInputTooLargeError):
            queue.enqueue(make_job("j1", input_path=str(big)))
        assert queue.size() == 0


def test_local_path_without_policy_is_rejected_before_any_write(
    tmp_path: Path,
) -> None:
    # Fail-closed: a local path must never be accepted just because no
    # validation policy was configured.
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    media = incoming / "clip.mp4"
    media.write_bytes(b"synthetic")
    db_path = tmp_path / "q.db"

    with SQLiteQueue(db_path) as queue:
        with pytest.raises(JobInputPolicyNotConfiguredError):
            queue.enqueue(make_job("j1", input_path=str(media)))
        # The rejection happens before the INSERT: no row exists.
        assert queue.size() == 0
        assert queue.get("j1") is None

    with raw_connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_local_path_is_rejected_when_trusted_root_is_not_configured(
    tmp_path: Path,
) -> None:
    # A policy without an explicit allowed_base_dir cannot prove containment,
    # so it must reject local paths rather than partially validate them.
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    media = incoming / "clip.mp4"
    media.write_bytes(b"synthetic")
    rootless_policy = EnqueuePolicy(
        max_file_size_bytes=1024,
        allowed_extensions=frozenset({".mp4"}),
        allowed_base_dir=None,
    )

    with pytest.raises(JobInputTrustedRootNotConfiguredError):
        validate_job_input(media, rootless_policy)

    with SQLiteQueue(tmp_path / "q.db", enqueue_policy=rootless_policy) as queue:
        with pytest.raises(JobInputTrustedRootNotConfiguredError):
            queue.enqueue(make_job("j1", input_path=str(media)))
        assert queue.size() == 0


def test_symlink_escaping_trusted_root_is_rejected(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    outside_dir = tmp_path / "elsewhere"
    outside_dir.mkdir()
    target = outside_dir / "clip.mp4"
    target.write_bytes(b"synthetic")
    # The link itself sits inside the trusted root but points outside.
    link = incoming / "link.mp4"
    link.symlink_to(target)
    policy = make_policy(incoming)

    with SQLiteQueue(tmp_path / "q.db", enqueue_policy=policy) as queue:
        with pytest.raises(JobInputPathNotAllowedError):
            queue.enqueue(make_job("j1", input_path=str(link)))
        assert queue.size() == 0


def test_all_path_rejections_leave_no_database_row(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    policy = make_policy(incoming, max_bytes=10)
    db_path = tmp_path / "q.db"

    cases = (
        incoming / "missing.mp4",  # nonexistent
        incoming / "notes.txt",  # disallowed extension
    )
    (incoming / "notes.txt").write_text("x")
    oversized = incoming / "big.mp4"
    oversized.write_bytes(b"x" * 50)

    with SQLiteQueue(db_path, enqueue_policy=policy) as queue:
        for index, path in enumerate((*cases, oversized)):
            with pytest.raises(InvalidJobInputError):
                queue.enqueue(make_job(f"j{index}", input_path=str(path)))
        assert queue.size() == 0

    with raw_connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


# --------------------------------------------------------------------------- #
# Opaque upload ids (remote API flow) — never treated as filesystem paths
# --------------------------------------------------------------------------- #


def test_upload_id_is_accepted_without_policy_and_never_hits_filesystem(
    tmp_path: Path,
) -> None:
    # No policy, no files on disk: an opaque id is a token, not a path.
    with SQLiteQueue(tmp_path / "q.db") as queue:
        queue.enqueue(QueuedJob(id="j1", type="split", upload_id="up-123_ABC.42"))
        claimed = queue.dequeue()
        assert claimed is not None
        assert claimed.upload_id == "up-123_ABC.42"
        assert claimed.input_path is None

    with raw_connect(tmp_path / "q.db") as conn:
        stored = conn.execute(
            "SELECT input_path FROM jobs WHERE id = 'j1'"
        ).fetchone()[0]
        assert stored == "upload://up-123_ABC.42"


def test_upload_id_rejects_path_like_or_malformed_tokens() -> None:
    for bad in (
        "",
        "a/b",
        "../etc/passwd",
        "a\\b",
        "up#1",
        "x" * 129,
        "a b",
        # Filesystem special names: dot-only strings must not pass even though
        # every character belongs to the allowed token charset.
        ".",
        "..",
        "...",
        "." * 16,
        "." * 128,
    ):
        with pytest.raises(InvalidUploadIdError):
            validate_upload_id(bad)
    for good in ("up-1", "abc123", "UPLOAD.123_x-1"):
        assert validate_upload_id(good) == good


def test_upload_id_allowed_charset_and_length_bounds() -> None:
    # Positive cases: every documented allowed character class, single char,
    # dots in non-special positions, and the inclusive 128-char upper bound.
    accepted = (
        "a",
        "A",
        "1",
        "up-123_ABC.42",
        "a.b-c_d",
        "a..b",
        ".lead",
        "trail.",
        "x" * 128,
    )
    for token in accepted:
        assert validate_upload_id(token) == token
        assert len(token) <= 128
    # Anything past 128 characters is malformed regardless of charset.
    with pytest.raises(InvalidUploadIdError):
        validate_upload_id("x" * 129)
    # Non-string types are rejected rather than coerced.
    for bad in (None, 123, b"up-1"):
        with pytest.raises(InvalidUploadIdError):
            validate_upload_id(bad)


def test_dot_only_upload_id_is_rejected_at_enqueue_without_a_database_row(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "q.db"
    with SQLiteQueue(db_path) as queue:
        for bad in (".", "..", "...", "." * 8):
            with pytest.raises(InvalidUploadIdError):
                queue.enqueue(QueuedJob(id=f"j-{bad}", type="split", upload_id=bad))
        assert queue.size() == 0

    # Row-level proof: validation runs before any INSERT, so the rejected
    # tokens never reach the jobs table (not even under the upload:// prefix).
    with raw_connect(db_path) as conn:
        rows = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        stored = conn.execute("SELECT input_path FROM jobs").fetchall()
    assert rows == 0
    assert stored == []


def test_input_path_and_upload_id_are_mutually_exclusive(tmp_path: Path) -> None:
    with SQLiteQueue(tmp_path / "q.db") as queue:
        with pytest.raises(ValueError, match="mutually exclusive"):
            queue.enqueue(
                QueuedJob(
                    id="j1",
                    type="split",
                    input_path="/tmp/x.mp4",
                    upload_id="up-1",
                )
            )
        assert queue.size() == 0


def test_jobs_without_any_input_reference_remain_supported(tmp_path: Path) -> None:
    # Internal/local scaffolding jobs may carry neither channel yet.
    with SQLiteQueue(tmp_path / "q.db") as queue:
        queue.enqueue(make_job("j1"))
        claimed = queue.dequeue()
        assert claimed is not None
        assert claimed.input_path is None
        assert claimed.upload_id is None


def test_empty_input_path_is_rejected_alone_and_with_upload_id(
    tmp_path: Path,
) -> None:
    # None means "no reference"; an empty string is malformed input and must
    # be rejected before any SQLite write in both channel combinations.
    db_path = tmp_path / "q.db"
    with SQLiteQueue(db_path) as queue:
        with pytest.raises(InvalidJobInputError):
            queue.enqueue(QueuedJob(id="j1", type="split", input_path=""))
        with pytest.raises(InvalidJobInputError):
            queue.enqueue(
                QueuedJob(
                    id="j2",
                    type="split",
                    input_path="",
                    upload_id="up-1",
                )
            )
        assert queue.size() == 0

    with raw_connect(db_path) as conn:
        rows = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        stored = conn.execute("SELECT input_path FROM jobs").fetchall()
    assert rows == 0
    assert stored == []


# --------------------------------------------------------------------------- #
# Guarded state transitions and fencing
# --------------------------------------------------------------------------- #


def test_lifecycle_transitions_require_claim_generation(tmp_path: Path) -> None:
    with SQLiteQueue(tmp_path / "q.db") as queue:
        queue.enqueue(make_job("j1"))
        job = queue.dequeue()
        assert job is not None

        queue.mark_processing("j1", generation=job.attempt)
        assert queue.get_status("j1") == "processing"
        queue.mark_failed("j1", generation=job.attempt, error_type="TOOL_ERROR")
        assert queue.get_status("j1") == "failed"

        queue.enqueue(make_job("j2"))
        claimed = queue.dequeue()
        assert claimed is not None
        queue.mark_completed("j2", generation=claimed.attempt)
        assert queue.get_status("j2") == "completed"

        with pytest.raises(JobNotFoundError):
            queue.mark_completed("missing", generation=0)

    with raw_connect(tmp_path / "q.db") as conn:
        failed = conn.execute(
            "SELECT error_type, completed_at FROM jobs WHERE id = 'j1'"
        ).fetchone()
        completed = conn.execute(
            "SELECT completed_at FROM jobs WHERE id = 'j2'"
        ).fetchone()
        assert failed["error_type"] == "TOOL_ERROR"
        assert completed["completed_at"] is not None


def test_transitions_are_rejected_from_illegal_states(tmp_path: Path) -> None:
    with SQLiteQueue(tmp_path / "q1.db") as queue:
        queue.enqueue(make_job("never_claimed"))
        # A queued job cannot be marked processing without a claim.
        with pytest.raises(InvalidJobStateError):
            queue.mark_processing("never_claimed", generation=0)

    with SQLiteQueue(tmp_path / "q2.db") as queue:
        queue.enqueue(make_job("j"))
        claimed = queue.dequeue()
        assert claimed is not None
        assert claimed.id == "j"
        queue.mark_completed("j", generation=claimed.attempt)
        # Terminal states are immutable.
        with pytest.raises(InvalidJobStateError):
            queue.mark_completed("j", generation=claimed.attempt)
        with pytest.raises(InvalidJobStateError):
            queue.mark_failed("j", generation=claimed.attempt)
        assert queue.get_status("j") == "completed"


def test_stale_generation_cannot_drive_transitions(tmp_path: Path) -> None:
    with SQLiteQueue(tmp_path / "q.db") as queue:
        queue.enqueue(make_job("j1"))
        first = queue.dequeue()
        assert first is not None
        queue.mark_processing("j1", generation=first.attempt)

        # Supervisor evidence: the original worker is dead. The supervisor
        # presents the claim generation it observed (first.attempt == 0).
        recovered = queue.recover_claimed_jobs(
            ["j1"],
            generation_by_id={"j1": first.attempt},
            reason="worker process exited",
        )
        assert recovered == 1
        second = queue.dequeue()
        assert second is not None
        assert second.attempt == first.attempt + 1
        queue.mark_processing("j1", generation=second.attempt)

        # The old worker waking up must not overwrite the new claim: every
        # transition guarded by its stale generation is rejected.
        with pytest.raises(InvalidJobStateError):
            queue.mark_processing("j1", generation=first.attempt)
        with pytest.raises(InvalidJobStateError):
            queue.mark_completed("j1", generation=first.attempt)
        with pytest.raises(InvalidJobStateError):
            queue.mark_failed("j1", generation=first.attempt)
        assert queue.get_status("j1") == "processing"

        # The current worker still owns the job and can finish it.
        queue.mark_completed("j1", generation=second.attempt)
        assert queue.get_status("j1") == "completed"

    with raw_connect(tmp_path / "q.db") as conn:
        row = conn.execute(
            "SELECT status, retry_count, completed_at FROM jobs WHERE id = 'j1'"
        ).fetchone()
        assert row["status"] == "completed"
        assert row["retry_count"] == 1
        assert row["completed_at"] is not None


# --------------------------------------------------------------------------- #
# Evidence-driven crash recovery
# --------------------------------------------------------------------------- #


def test_recover_claimed_jobs_requeues_with_audit_event(tmp_path: Path) -> None:
    db_path = tmp_path / "q.db"
    with SQLiteQueue(db_path) as queue:
        queue.enqueue(make_job("j1"))
        claimed = queue.dequeue()
        assert claimed is not None
        queue.mark_processing("j1", generation=claimed.attempt)

        recovered = queue.recover_claimed_jobs(
            ["j1"],
            generation_by_id={"j1": claimed.attempt},
            reason="heartbeat lost: environment offline",
        )
        assert recovered == 1
        assert queue.get_status("j1") == "queued"

    with raw_connect(db_path) as conn:
        job = conn.execute(
            "SELECT retry_count, started_at FROM jobs WHERE id = 'j1'"
        ).fetchone()
        assert job["retry_count"] == 1
        assert job["started_at"] is None
        event = conn.execute(
            "SELECT event_type, payload_json FROM job_events WHERE job_id = 'j1'"
        ).fetchone()
        assert event["event_type"] == "job_requeued"
        payload = json.loads(event["payload_json"])
        # ``reason`` is audit text stored verbatim; the queue never parses
        # or verifies it as liveness evidence.
        assert payload["reason"] == "heartbeat lost: environment offline"
        assert payload["previous_status"] == "processing"
        assert payload["attempt"] == 0


def test_recover_fails_job_after_retry_budget_exhausted(tmp_path: Path) -> None:
    db_path = tmp_path / "q.db"
    with SQLiteQueue(db_path) as queue:
        queue.enqueue(make_job("j_exhausted"))
        claimed = queue.dequeue()
        assert claimed is not None
        queue.mark_processing("j_exhausted", generation=claimed.attempt)
        with raw_connect(db_path) as conn:
            conn.execute(
                "UPDATE jobs SET retry_count = 3 WHERE id = 'j_exhausted'"
            )
            conn.commit()

        # The supervisor observed the claim generation currently stored
        # (retry_count was raised to 3 to simulate the exhausted budget).
        recovered = queue.recover_claimed_jobs(
            ["j_exhausted"],
            generation_by_id={"j_exhausted": 3},
            reason="worker OOM kill confirmed",
        )
        assert recovered == 1
        assert queue.get_status("j_exhausted") == "failed"

    with raw_connect(db_path) as conn:
        row = conn.execute(
            "SELECT retry_count FROM jobs WHERE id = 'j_exhausted'"
        ).fetchone()
        assert row["retry_count"] == 4
        event = conn.execute(
            "SELECT event_type FROM job_events WHERE job_id = 'j_exhausted'"
        ).fetchone()
        assert event["event_type"] == "job_failed"


def test_recover_ignores_unclaimed_and_unknown_jobs(tmp_path: Path) -> None:
    with SQLiteQueue(tmp_path / "q.db") as queue:
        queue.enqueue(make_job("queued_only"))
        queue.enqueue(make_job("claimed"))
        claimed = queue.dequeue()
        assert claimed is not None

        # A job that was never claimed is not stalled and stays untouched.
        recovered = queue.recover_claimed_jobs(
            ["queued_only", "claimed"],
            generation_by_id={"queued_only": 0, "claimed": claimed.attempt},
            reason="supervisor restart",
        )
        assert recovered == 1
        assert queue.get_status("queued_only") == "queued"
        assert queue.get_status("claimed") == "queued"

        # Re-recovering the now-queued job is a safe no-op: nothing claimed,
        # no rows changed, no events written.
        assert (
            queue.recover_claimed_jobs(
                ["queued_only"],
                generation_by_id={"queued_only": 0},
                reason="duplicate call",
            )
            == 0
        )
        assert queue.get_status("queued_only") == "queued"

        with pytest.raises(JobNotFoundError):
            queue.recover_claimed_jobs(
                ["ghost"], generation_by_id={"ghost": 0}, reason="no evidence"
            )
        with pytest.raises(ValueError, match="reason"):
            queue.recover_claimed_jobs(
                ["claimed"], generation_by_id={"claimed": 0}, reason="   "
            )
        with pytest.raises(ValueError, match="empty"):
            queue.recover_claimed_jobs([], generation_by_id={}, reason="nothing")
        # The supervisor must prove the observed generation for every job.
        with pytest.raises(ValueError, match="generation"):
            queue.recover_claimed_jobs(
                ["claimed"], generation_by_id={}, reason="missing fencing token"
            )
        with pytest.raises(TypeError):
            queue.recover_claimed_jobs(
                ["claimed"],
                generation_by_id=[("claimed", 0)],  # type: ignore[dict-item]
                reason="mapping required",
            )


def test_recovery_with_correct_generation_reclaims_and_logs_one_event(
    tmp_path: Path,
) -> None:
    """Audit scenarios (a) and (c): a recovery matching the observed claim
    generation succeeds, moves the job back to queued and writes exactly one
    audit event; the job is then re-claimable under a new generation."""
    db_path = tmp_path / "q.db"
    with SQLiteQueue(db_path) as queue:
        queue.enqueue(make_job("j1"))
        first = queue.dequeue()
        assert first is not None
        assert first.attempt == 0
        queue.mark_processing("j1", generation=first.attempt)

        recovered = queue.recover_claimed_jobs(
            ["j1"],
            generation_by_id={"j1": first.attempt},
            reason="worker heartbeats lost",
        )
        assert recovered == 1
        assert queue.get_status("j1") == "queued"

        second = queue.dequeue()
        assert second is not None
        assert second.attempt == 1

    with raw_connect(db_path) as conn:
        events = conn.execute(
            "SELECT event_type FROM job_events WHERE job_id = 'j1'"
        ).fetchall()
    assert [row["event_type"] for row in events] == ["job_requeued"]


def test_stale_recovery_generation_is_a_noop_without_event_or_state_change(
    tmp_path: Path,
) -> None:
    """Audit scenario (b): after gen-0 recovery and a gen-1 re-claim, a
    duplicated supervisor notification still presenting generation 0 must not
    touch the newer worker's state and must not write a recovery event."""
    db_path = tmp_path / "q.db"
    with SQLiteQueue(db_path) as queue:
        queue.enqueue(make_job("j1"))
        first = queue.dequeue()
        assert first is not None
        # First, genuine recovery at generation 0, then a fresh gen-1 claim.
        assert (
            queue.recover_claimed_jobs(
                ["j1"],
                generation_by_id={"j1": first.attempt},
                reason="first crash",
            )
            == 1
        )
        second = queue.dequeue()
        assert second is not None
        assert second.attempt == 1
        queue.mark_processing("j1", generation=second.attempt)

        # Stale duplicate notification still carrying the dead generation 0.
        recovered = queue.recover_claimed_jobs(
            ["j1"],
            generation_by_id={"j1": 0},
            reason="stale duplicate notification",
        )
        assert recovered == 0
        assert queue.get_status("j1") == "processing"
        job = queue.get("j1")
        assert job is not None
        assert job.attempt == 1

    with raw_connect(db_path) as conn:
        row = conn.execute(
            "SELECT status, retry_count, started_at FROM jobs WHERE id = 'j1'"
        ).fetchone()
        assert row["status"] == "processing"
        assert row["retry_count"] == 1
        assert row["started_at"] is not None
        events = conn.execute(
            "SELECT event_type, payload_json FROM job_events WHERE job_id = 'j1'"
        ).fetchall()
    # Exactly one event exists — from the genuine gen-0 recovery only.
    assert len(events) == 1
    assert events[0]["event_type"] == "job_requeued"
    assert json.loads(events[0]["payload_json"])["reason"] == "first crash"


def test_recovery_mixed_batch_skips_stale_generation_and_recovers_live_claim(
    tmp_path: Path,
) -> None:
    """One batch containing a stale entry and a valid entry must recover only
    the job whose generation matches, emitting exactly one event."""
    db_path = tmp_path / "q.db"
    with SQLiteQueue(db_path) as queue:
        queue.enqueue(make_job("j_stale"))
        queue.enqueue(make_job("j_live"))

        stale_claim = queue.dequeue()
        live_claim = queue.dequeue()
        assert stale_claim is not None and live_claim is not None
        assert stale_claim.id == "j_stale"
        queue.mark_processing("j_stale", generation=stale_claim.attempt)
        queue.mark_processing("j_live", generation=live_claim.attempt)

        # j_stale is genuinely recovered once and re-claimed at generation 1.
        assert (
            queue.recover_claimed_jobs(
                ["j_stale"],
                generation_by_id={"j_stale": stale_claim.attempt},
                reason="j_stale first crash",
            )
            == 1
        )
        stale_reclaim = queue.dequeue()
        assert stale_reclaim is not None and stale_reclaim.attempt == 1
        queue.mark_processing("j_stale", generation=stale_reclaim.attempt)

        # Batch where the supervisor holds a stale gen-0 view of j_stale but
        # the correct generation-0 view of j_live.
        recovered = queue.recover_claimed_jobs(
            ["j_stale", "j_live"],
            generation_by_id={"j_stale": 0, "j_live": live_claim.attempt},
            reason="supervisor batch after confirmed deaths",
        )
        assert recovered == 1
        assert queue.get_status("j_stale") == "processing"
        assert queue.get_status("j_live") == "queued"

    with raw_connect(db_path) as conn:
        stale_events = conn.execute(
            "SELECT COUNT(*) FROM job_events WHERE job_id = 'j_stale'"
        ).fetchone()[0]
        live_events = conn.execute(
            "SELECT event_type FROM job_events WHERE job_id = 'j_live'"
        ).fetchall()
    # j_stale keeps only its first recovery event; the stale batch entry
    # added nothing. j_live got exactly its one genuine requeue event.
    assert stale_events == 1
    assert [row["event_type"] for row in live_events] == ["job_requeued"]


def test_recovery_rejects_invalid_generation_values(tmp_path: Path) -> None:
    with SQLiteQueue(tmp_path / "q.db") as queue:
        queue.enqueue(make_job("j1"))
        claim = queue.dequeue()
        assert claim is not None
        for bad_generation in (-1, 1.5, True, "0"):
            with pytest.raises((ValueError, TypeError)):
                queue.recover_claimed_jobs(
                    ["j1"],
                    generation_by_id={"j1": bad_generation},  # type: ignore[dict-item]
                    reason="invalid fencing token",
                )
        # The rejected calls changed nothing.
        assert queue.get_status("j1") == "assigned"


def test_recovery_rejects_duplicate_job_ids_without_any_write(
    tmp_path: Path,
) -> None:
    """A duplicated id is a supervisor bug: the call must fail before any
    write, leaving the claim intact and exactly one valid recovery possible."""
    db_path = tmp_path / "q.db"
    with SQLiteQueue(db_path) as queue:
        queue.enqueue(make_job("j1"))
        claim = queue.dequeue()
        assert claim is not None

        with pytest.raises(ValueError, match="duplicate"):
            queue.recover_claimed_jobs(
                ["j1", "j1"],
                generation_by_id={"j1": claim.attempt},
                reason="buggy duplicate supervisor batch",
            )
        # Nothing moved: the original claim is still active.
        assert queue.get_status("j1") == "assigned"
        job = queue.get("j1")
        assert job is not None and job.attempt == 0

        # The same job can still be recovered exactly once via a valid call.
        assert (
            queue.recover_claimed_jobs(
                ["j1"],
                generation_by_id={"j1": claim.attempt},
                reason="single valid recovery",
            )
            == 1
        )

    with raw_connect(db_path) as conn:
        row = conn.execute(
            "SELECT status, retry_count FROM jobs WHERE id = 'j1'"
        ).fetchone()
        events = conn.execute(
            "SELECT event_type, payload_json FROM job_events WHERE job_id = 'j1'"
        ).fetchall()
    assert row["status"] == "queued"
    assert row["retry_count"] == 1
    # Exactly one event, from the valid recovery only — no spurious duplicate.
    assert [event["event_type"] for event in events] == ["job_requeued"]
    assert json.loads(events[0]["payload_json"])["reason"] == "single valid recovery"


def test_no_automatic_time_based_rescheduling_exists(tmp_path: Path) -> None:
    # Regression guard for the audit finding: the queue must expose no method
    # that requeues a job purely because started_at grew old.
    assert not hasattr(SQLiteQueue, "recover_stalled")
    db_path = tmp_path / "q.db"
    with SQLiteQueue(db_path) as queue:
        queue.enqueue(make_job("old_job"))
        claimed = queue.dequeue()
        assert claimed is not None
        queue.mark_processing("old_job", generation=claimed.attempt)

    # Even an ancient started_at must not move the job on its own.
    with raw_connect(db_path) as conn:
        conn.execute(
            "UPDATE jobs SET started_at = datetime('now', '-1 day') "
            "WHERE id = 'old_job'"
        )
        conn.commit()
    with SQLiteQueue(db_path) as restarted:
        assert restarted.get_status("old_job") == "processing"
        assert restarted.size() == 0


def test_recovery_has_no_automatic_call_site() -> None:
    # Regression guard: recovery must stay an explicit supervisor decision.
    # No production module may invoke recover_claimed_jobs itself.
    package = REPO_ROOT / "orchestrator"
    callers: list[str] = []
    for source in package.rglob("*.py"):
        text = source.read_text(encoding="utf-8")
        if "recover_claimed_jobs" in text and "def recover_claimed_jobs" not in text:
            callers.append(str(source.relative_to(REPO_ROOT)))
    assert callers == [], f"unexpected automatic recovery callers: {callers}"
    # The definition itself lives exactly once, in the queue module.
    queue_source = (package / "queue.py").read_text(encoding="utf-8")
    assert queue_source.count("def recover_claimed_jobs") == 1


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

        for table in ("jobs", "job_events"):
            canonical_cols = canonical.execute(
                f"PRAGMA table_info({table})"
            ).fetchall()
            embedded_cols = embedded.execute(f"PRAGMA table_info({table})").fetchall()
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

        # Referenced parent tables must exist.
        for table in ("environments", "job_events"):
            assert embedded.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone() is not None
    canonical.close()
