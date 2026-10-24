"""API tests for the durable job endpoints wired in ``orchestrator/main.py``.

Every test uses an isolated temporary SQLite database and a local bearer
token; no network calls, production data or paid resources are involved.
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from orchestrator.main import app, get_queue
from orchestrator.settings import get_settings

TOKEN = "test-bearer-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
VALID_SPEC = {"type": "split", "file": "up-123_ABC.42", "priority": 5}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path) -> TestClient:
    db_path = tmp_path / "api.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("BEARER_TOKEN", TOKEN)
    get_settings.cache_clear()
    with TestClient(app) as client:
        yield client
    get_settings.cache_clear()


class _FailingQueue:
    """Stand-in whose data-access calls always fail like an unavailable DB."""

    def enqueue(self, job) -> None:  # pragma: no cover - exercised below
        raise sqlite3.OperationalError("simulated disk failure")

    def fetch_job(self, job_id: str):  # pragma: no cover - exercised below
        raise sqlite3.OperationalError("simulated disk failure")

    def list_jobs(self, *, status: str | None = None, limit: int = 50):  # pragma: no cover
        raise sqlite3.OperationalError("simulated disk failure")


# --------------------------------------------------------------------------- #
# Health and authentication
# --------------------------------------------------------------------------- #


def test_health_is_open_and_reports_degraded(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "degraded"


def test_jobs_endpoints_require_bearer_token(client: TestClient) -> None:
    assert client.get("/jobs").status_code == 401
    assert client.get("/jobs/j1").status_code == 401
    assert client.post("/jobs", json=VALID_SPEC).status_code == 401

    assert client.get("/jobs", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/jobs", headers={"Authorization": "Token abc"}).status_code == 401

    # Correct token reaches the handler (empty list is a normal 200).
    assert client.get("/jobs", headers=AUTH).status_code == 200


def test_api_fails_closed_when_no_bearer_token_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "closed.db"))
    monkeypatch.setenv("BEARER_TOKEN", "")
    get_settings.cache_clear()
    with TestClient(app) as unconfigured:
        response = unconfigured.get("/jobs", headers=AUTH)
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
    get_settings.cache_clear()


# --------------------------------------------------------------------------- #
# POST /jobs
# --------------------------------------------------------------------------- #


def test_create_job_is_accepted_persisted_and_retrievable(client: TestClient) -> None:
    response = client.post(
        "/jobs",
        headers=AUTH,
        json={
            "type": "transcription",
            "file": "up-77",
            "language": "ar",
            "environment": "vps",
            "cloud_consent": True,
            "priority": 7,
            "preset": "balanced",
        },
    )
    assert response.status_code == 202
    body = response.json()
    job_id = body["id"]
    assert body["status"] == "queued"
    assert body["type"] == "transcription"
    assert body["priority"] == 7
    assert body["retry_count"] == 0
    assert body["output_paths"] == []
    assert body["environment"] == "vps"
    assert body["created_at"].endswith("Z")

    detail = client.get(f"/jobs/{job_id}", headers=AUTH)
    assert detail.status_code == 200
    assert detail.json()["id"] == job_id

    listed = client.get("/jobs", headers=AUTH)
    assert listed.status_code == 200
    assert [job["id"] for job in listed.json()] == [job_id]


def test_create_job_persists_upload_prefix_and_checkpoint_without_fs_path(
    client: TestClient, tmp_path
) -> None:
    response = client.post("/jobs", headers=AUTH, json=VALID_SPEC)
    assert response.status_code == 202
    job_id = response.json()["id"]

    import sqlite3

    raw = sqlite3.connect(tmp_path / "api.db")
    row = raw.execute(
        "SELECT input_path, checkpoint_data, environment FROM jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    raw.close()
    assert row[0] == "upload://up-123_ABC.42"
    assert "vps" not in row[1]  # no environment preference in this request
    assert row[2] is None  # FK column stays NULL until a worker is assigned


@pytest.mark.parametrize(
    "upload_id",
    [".", "..", "...", "a/b", "../etc/passwd", "a\\b", "up 1", "x" * 129],
)
def test_create_job_rejects_malformed_upload_ids(
    client: TestClient, upload_id: str
) -> None:
    response = client.post(
        "/jobs", headers=AUTH, json={"type": "split", "file": upload_id}
    )
    assert response.status_code == 400
    assert client.get("/jobs", headers=AUTH).json() == []


@pytest.mark.parametrize(
    "spec",
    [
        {"type": "unknown", "file": "up-1"},
        {"type": "split", "file": "up-1", "priority": 11},
        {"type": "split", "file": "up-1", "unexpected": True},
        {"type": "split", "file": "up-1", "mask_region": {
            "top": 0.8, "bottom": 0.5, "left": 0.1, "right": 0.9}},
        {"type": "split", "file": "up-1", "mask_region": {
            "top": 0.1, "bottom": 0.9, "left": 0.9, "right": 0.1}},
        {"type": "split"},  # missing required file
    ],
)
def test_create_job_rejects_schema_violations(
    client: TestClient, spec: dict
) -> None:
    response = client.post("/jobs", headers=AUTH, json=spec)
    assert response.status_code == 422
    assert client.get("/jobs", headers=AUTH).json() == []


def test_create_job_accepts_valid_mask_region(client: TestClient) -> None:
    response = client.post(
        "/jobs",
        headers=AUTH,
        json={
            "type": "text_removal",
            "file": "up-9",
            "mask_region": {"top": 0.82, "bottom": 1.0, "left": 0.0, "right": 1.0},
        },
    )
    assert response.status_code == 202


def test_duplicate_generated_id_is_mapped_to_conflict(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import orchestrator.main as main_module

    monkeypatch.setattr(
        main_module.uuid, "uuid4", lambda: SimpleNamespace(hex="fixed-hex-id")
    )
    first = client.post("/jobs", headers=AUTH, json=VALID_SPEC)
    assert first.status_code == 202
    second = client.post("/jobs", headers=AUTH, json=VALID_SPEC)
    assert second.status_code == 409


# --------------------------------------------------------------------------- #
# GET /jobs and GET /jobs/{id}
# --------------------------------------------------------------------------- #


def test_list_jobs_orders_by_priority_and_filters_status(client: TestClient) -> None:
    for index, priority in enumerate((1, 9, 5, 9)):
        client.post(
            "/jobs",
            headers=AUTH,
            json={"type": "split", "file": f"up-{index}", "priority": priority},
        )
    jobs = client.get("/jobs", headers=AUTH).json()
    assert [job["priority"] for job in jobs] == [9, 9, 5, 1]

    only_queued = client.get("/jobs?status=queued", headers=AUTH)
    assert only_queued.status_code == 200
    assert len(only_queued.json()) == 4

    bad_filter = client.get("/jobs?status=bogus", headers=AUTH)
    assert bad_filter.status_code == 400

    limited = client.get("/jobs?limit=2", headers=AUTH)
    assert limited.status_code == 200
    assert len(limited.json()) == 2

    out_of_range = client.get("/jobs?limit=201", headers=AUTH)
    assert out_of_range.status_code == 422


def test_get_unknown_job_returns_404(client: TestClient) -> None:
    response = client.get("/jobs/does-not-exist", headers=AUTH)
    assert response.status_code == 404
    assert response.json()["detail"] == "job not found"


def test_database_failures_return_503_without_internal_details(
    client: TestClient,
) -> None:
    app.dependency_overrides[get_queue] = lambda: _FailingQueue()
    try:
        created = client.post("/jobs", headers=AUTH, json=VALID_SPEC)
        assert created.status_code == 503
        assert created.json()["detail"] == "job store temporarily unavailable"
        assert "simulated" not in created.text

        listing = client.get("/jobs", headers=AUTH)
        assert listing.status_code == 503

        detail = client.get("/jobs/j1", headers=AUTH)
        assert detail.status_code == 503
    finally:
        app.dependency_overrides.clear()


def test_iso_timestamp_helper_normalizes_sqlite_stamps() -> None:
    from orchestrator.main import _iso_timestamp

    assert _iso_timestamp(None) is None
    assert (
        _iso_timestamp("2026-10-24 19:42:36") == "2026-10-24T19:42:36Z"
    )
    # Stamps already carrying an ISO shape are returned unchanged.
    exotic = "2026-10-24T19:42:36+03:00"
    assert _iso_timestamp(exotic) == exotic


def test_jobs_survive_a_service_restart(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    db_path = tmp_path / "durable.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("BEARER_TOKEN", TOKEN)
    get_settings.cache_clear()

    with TestClient(app) as first:
        response = first.post("/jobs", headers=AUTH, json=VALID_SPEC)
        assert response.status_code == 202
        job_id = response.json()["id"]

    # A brand-new app/queue instance over the same database file sees the job.
    get_settings.cache_clear()
    with TestClient(app) as second:
        response = second.get(f"/jobs/{job_id}", headers=AUTH)
        assert response.status_code == 200
        assert response.json()["status"] == "queued"
    get_settings.cache_clear()


# --------------------------------------------------------------------------- #
# Audit follow-up: cloud consent, mandatory mask, checkpoint robustness
# --------------------------------------------------------------------------- #


def test_cloud_environment_requires_explicit_consent_and_writes_no_row(
    client: TestClient, tmp_path
) -> None:
    forbidden = client.post(
        "/jobs",
        headers=AUTH,
        json={
            "type": "split",
            "file": "up-cloud-1",
            "environment": "cloud",
            "cloud_consent": False,
        },
    )
    assert forbidden.status_code == 403
    assert "cloud_consent" in forbidden.json()["detail"]

    raw = sqlite3.connect(tmp_path / "api.db")
    try:
        assert raw.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    finally:
        raw.close()

    # Explicit consent unlocks cloud, and non-cloud environments stay open.
    consented = client.post(
        "/jobs",
        headers=AUTH,
        json={
            "type": "split",
            "file": "up-cloud-2",
            "environment": "cloud",
            "cloud_consent": True,
        },
    )
    assert consented.status_code == 202
    vps = client.post(
        "/jobs",
        headers=AUTH,
        json={"type": "split", "file": "up-vps-1", "environment": "vps"},
    )
    assert vps.status_code == 202


def test_text_removal_requires_mask_region_before_any_write(
    client: TestClient, tmp_path
) -> None:
    rejected = client.post(
        "/jobs",
        headers=AUTH,
        json={"type": "text_removal", "file": "up-mask-1"},
    )
    assert rejected.status_code == 422

    raw = sqlite3.connect(tmp_path / "api.db")
    try:
        assert raw.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    finally:
        raw.close()

    # A valid mask makes the same job type acceptable, and other job types
    # remain usable without a mask.
    accepted = client.post(
        "/jobs",
        headers=AUTH,
        json={
            "type": "text_removal",
            "file": "up-mask-2",
            "mask_region": {"top": 0.82, "bottom": 1.0, "left": 0.0, "right": 1.0},
        },
    )
    assert accepted.status_code == 202
    split = client.post(
        "/jobs", headers=AUTH, json={"type": "split", "file": "up-mask-3"}
    )
    assert split.status_code == 202


@pytest.mark.parametrize("corrupt_value", ['{broken json', "123", "[1, 2, 3]", "null"])
def test_corrupt_checkpoint_data_returns_controlled_response_not_500(
    client: TestClient, tmp_path, corrupt_value: str
) -> None:
    created = client.post("/jobs", headers=AUTH, json=VALID_SPEC)
    assert created.status_code == 202
    job_id = created.json()["id"]

    # Simulate a row whose checkpoint column was corrupted by a direct edit.
    raw = sqlite3.connect(tmp_path / "api.db")
    try:
        raw.execute(
            "UPDATE jobs SET checkpoint_data = ? WHERE id = ?",
            (corrupt_value, job_id),
        )
        raw.commit()
    finally:
        raw.close()

    response = client.get(f"/jobs/{job_id}", headers=AUTH)
    # Graceful degradation: a normal Job payload with no environment hint,
    # never an uncontrolled 500 and no parser internals in the response.
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == job_id
    assert body["environment"] is None
    # No parser internals or tracebacks leak into the controlled response.
    assert "JSONDecodeError" not in response.text
    assert "Traceback" not in response.text


def test_checkpoint_payload_helper_handles_every_corrupt_shape() -> None:
    from orchestrator.main import _load_checkpoint_payload

    # Empty/None means no checkpoint at all.
    assert _load_checkpoint_payload(None) == {}
    assert _load_checkpoint_payload("") == {}
    # Malformed JSON and non-object JSON degrade to an empty mapping.
    assert _load_checkpoint_payload("{broken") == {}
    assert _load_checkpoint_payload("123") == {}
    assert _load_checkpoint_payload("[1, 2]") == {}
    assert _load_checkpoint_payload("null") == {}
    # A valid object is preserved, including the environment preference.
    assert _load_checkpoint_payload('{"environment":"cloud"}') == {
        "environment": "cloud"
    }
    # Non-string defensive input must not raise either.
    assert _load_checkpoint_payload(123) == {}  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Audit follow-up: corrupt output_paths and invalid environment values
# --------------------------------------------------------------------------- #


def _create_job_and_corrupt(client: TestClient, tmp_path, column: str, value: str) -> str:
    created = client.post("/jobs", headers=AUTH, json=VALID_SPEC)
    assert created.status_code == 202
    job_id = created.json()["id"]
    raw = sqlite3.connect(tmp_path / "api.db")
    try:
        raw.execute(f"UPDATE jobs SET {column} = ? WHERE id = ?", (value, job_id))
        raw.commit()
    finally:
        raw.close()
    return job_id


@pytest.mark.parametrize(
    "corrupt_outputs",
    [
        "{broken json",  # malformed JSON
        "123",  # valid JSON number, not a list
        '{"not":"a list"}',  # valid JSON object, not a list
        "null",  # explicit JSON null
        "[1, 2, 3]",  # a list with no string elements
    ],
)
def test_corrupt_output_paths_never_returns_500(
    client: TestClient, tmp_path, corrupt_outputs: str
) -> None:
    job_id = _create_job_and_corrupt(client, tmp_path, "output_paths", corrupt_outputs)

    detail = client.get(f"/jobs/{job_id}", headers=AUTH)
    assert detail.status_code == 200
    assert detail.json()["output_paths"] == []

    listing = client.get("/jobs", headers=AUTH)
    assert listing.status_code == 200
    listed = next(job for job in listing.json() if job["id"] == job_id)
    assert listed["output_paths"] == []


def test_valid_output_paths_still_serialize(client: TestClient, tmp_path) -> None:
    job_id = _create_job_and_corrupt(
        client, tmp_path, "output_paths", '["/out/a.mp4", "/out/b.mp4"]'
    )
    response = client.get(f"/jobs/{job_id}", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["output_paths"] == ["/out/a.mp4", "/out/b.mp4"]


@pytest.mark.parametrize("bad_environment", ['"datacenter"', "123", "null", '["cloud"]'])
def test_invalid_checkpoint_environment_degrades_to_none(
    client: TestClient, tmp_path, bad_environment: str
) -> None:
    job_id = _create_job_and_corrupt(
        client, tmp_path, "checkpoint_data", f'{{"environment": {bad_environment}}}'
    )
    response = client.get(f"/jobs/{job_id}", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["environment"] is None

    listing = client.get("/jobs", headers=AUTH)
    assert listing.status_code == 200
    listed = next(job for job in listing.json() if job["id"] == job_id)
    assert listed["environment"] is None


def test_valid_checkpoint_environment_is_preserved(
    client: TestClient, tmp_path
) -> None:
    job_id = _create_job_and_corrupt(
        client, tmp_path, "checkpoint_data", '{"environment": "cloud"}'
    )
    response = client.get(f"/jobs/{job_id}", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["environment"] == "cloud"
