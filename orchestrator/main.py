"""FastAPI application wiring the durable job queue to the HTTP contract.

Implemented in this layer (D2 scope):

* Bearer-token authentication matching the global ``BearerAuth`` OpenAPI
  security scheme. The service fails closed: with no ``BEARER_TOKEN``
  configured every protected request is rejected with 401.
* ``POST /jobs`` — accepts only opaque upload ids (never server filesystem
  paths), validates them with the queue's token rule and durably enqueues.
* ``GET /jobs`` — parameterized status filtering with the contract ordering
  ``priority DESC, created_at ASC`` and a bounded limit.
* ``GET /jobs/{id}`` — full stored job record or 404.

Out of scope here: ``POST /uploads``, SSE progress, cancellation, downloads,
account/agent routes and all FFmpeg processing — those remain separate
deliverables. ``/health`` actively probes the database (503 on failure,
without internal details); worker liveness is not monitored until the D3
heartbeat protocol exists.
"""

from __future__ import annotations

import hmac
import json
import sqlite3
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from orchestrator.queue import (
    InvalidUploadIdError,
    JobAlreadyExistsError,
    JobRecord,
    QueuedJob,
    SQLiteQueue,
    validate_upload_id,
)
from orchestrator.settings import Settings, get_settings

# FastAPI dependency alias. Module-level Annotated aliases keep framework
# dependency calls out of function default arguments (ruff B008).
SettingsDep = Annotated[Settings, Depends(get_settings)]

# Mirrors the status enum in D1_FOUNDATION/03_API_SPEC.yaml.
JOB_STATUSES: frozenset[str] = frozenset(
    {
        "queued",
        "assigned",
        "uploading",
        "processing",
        "downloading",
        "completed",
        "failed",
        "cancelled",
        "stalled",
    }
)


class MaskRegion(BaseModel):
    """Normalized rectangle (0..1); the lower/right edges must be strict."""

    model_config = ConfigDict(extra="forbid")

    top: float = Field(ge=0, le=1)
    bottom: float = Field(ge=0, le=1)
    left: float = Field(ge=0, le=1)
    right: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _validate_geometry(self) -> MaskRegion:
        if self.bottom <= self.top:
            raise ValueError("bottom must be strictly greater than top")
        if self.right <= self.left:
            raise ValueError("right must be strictly greater than left")
        return self


class JobSpec(BaseModel):
    """Remote job creation request; ``file`` is an opaque upload id."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["split", "transcription", "text_removal"]
    file: str = Field(
        min_length=1,
        description="Server-issued upload ID, never an arbitrary server path",
    )
    language: str = "auto"
    environment: Literal["phone", "vps", "cloud"] | None = None
    cloud_consent: bool = False
    priority: int = Field(default=5, ge=0, le=10)
    preset: Literal["fast", "balanced", "high_quality"] | None = None
    mask_region: MaskRegion | None = None

    @model_validator(mode="after")
    def _require_mask_for_text_removal(self) -> JobSpec:
        # The reviewed-OCR-mask exception path mentioned in the OpenAPI
        # description is not represented in this model yet, so text_removal
        # jobs must always carry an explicit mask region. Rejected at request
        # validation (HTTP 422), before any queue or database interaction.
        if self.type == "text_removal" and self.mask_region is None:
            raise ValueError(
                "mask_region is required for text_removal jobs until the "
                "reviewed OCR mask workflow is implemented"
            )
        return self


class Job(BaseModel):
    """Job representation returned by the API, aligned with the OpenAPI Job."""

    id: str
    status: str
    type: str
    created_at: str
    output_paths: list[str] = Field(default_factory=list)
    environment: str | None = None
    priority: int
    progress_pct: float = 0
    error_type: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    retry_count: int = 0


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Open the durable queue at startup and close it on shutdown."""
    settings = get_settings()
    queue = SQLiteQueue(settings.database_path)
    app.state.queue = queue
    try:
        yield
    finally:
        queue.close()


app = FastAPI(
    title="AetherFlow Orchestrator",
    version="0.1.0",
    description=(
        "Durable job-queue API: authenticated job creation, listing and "
        "detail lookup. Uploads, streaming and processing are separate "
        "deliverables."
    ),
    lifespan=lifespan,
)


def require_bearer(
    settings: SettingsDep,
    authorization: str | None = Header(default=None),
) -> None:
    """Validate the Bearer token; fail closed when the server has no token."""
    expected = settings.bearer_token
    if not expected or not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token or not hmac.compare_digest(
        token, expected
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_queue(request: Request) -> SQLiteQueue:
    """FastAPI dependency returning the request-scoped durable queue."""
    return request.app.state.queue


# Defined after get_queue so the alias can reference the concrete dependency.
QueueDep = Annotated[SQLiteQueue, Depends(get_queue)]


def _iso_timestamp(value: str | None) -> str | None:
    """Normalize SQLite's ``YYYY-MM-DD HH:MM:SS`` stamps to ISO-8601 UTC."""
    if not value:
        return None
    try:
        return (
            datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
            .replace(tzinfo=UTC)
            .isoformat()
            .replace("+00:00", "Z")
        )
    except ValueError:
        # Tolerate stamps that were supplied explicitly in another ISO shape.
        return value


def _load_checkpoint_payload(raw: str | None) -> dict:
    """Parse stored checkpoint JSON defensively.

    Rows are normally written by this service via ``json.dumps``, so invalid
    content can only originate from a direct/manual database edit. The
    controlled behavior is graceful degradation: an empty value, malformed
    JSON or a non-object JSON value yields an empty mapping, so the API keeps
    returning a normal Job response instead of an uncontrolled 500 and never
    leaks parser internals. This function touches no database connection, so
    genuine SQLite errors are not swallowed here.
    """
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


# Allowed environment preference values, mirroring the JobSpec/OpenAPI enum.
_ALLOWED_ENVIRONMENTS: frozenset[str] = frozenset({"phone", "vps", "cloud"})


def _safe_environment(payload: dict) -> str | None:
    """Return the checkpoint environment only when it is a valid enum value.

    A missing key, a non-string value or an unknown string all degrade to
    ``None`` rather than leaking an uncontrolled value into the Job model.
    """
    value = payload.get("environment")
    return value if isinstance(value, str) and value in _ALLOWED_ENVIRONMENTS else None


def _safe_output_references(output_paths: tuple[str, ...]) -> list[str]:
    """Return only storage-root-relative output references.

    The worker writes references relative to its private output base (e.g.
    ``<job-id>/part_000.mp4``); absolute filesystem paths must never appear in
    an API response, as they would disclose the server's storage layout. This
    read-side guard is defence in depth: any absolute or parent-escaping
    entry — only reachable through a direct database edit, since the worker
    cannot write one — is omitted rather than leaked.
    """
    safe: list[str] = []
    for entry in output_paths:
        if not isinstance(entry, str) or not entry:
            continue
        candidate = Path(entry)
        if candidate.is_absolute() or ".." in candidate.parts:
            continue
        safe.append(entry)
    return safe


def _record_to_job(record: JobRecord) -> Job:
    """Map a full queue record onto the public Job response model."""
    payload = _load_checkpoint_payload(record.checkpoint_data)
    return Job(
        id=record.id,
        status=record.status,
        type=record.type,
        created_at=_iso_timestamp(record.created_at) or record.created_at,
        output_paths=_safe_output_references(record.output_paths),
        environment=_safe_environment(payload),
        priority=record.priority,
        error_type=record.error_type,
        started_at=_iso_timestamp(record.started_at),
        completed_at=_iso_timestamp(record.completed_at),
        retry_count=record.retry_count,
    )


@app.post(
    "/jobs",
    response_model=Job,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_bearer)],
)
def create_job(spec: JobSpec, queue: QueueDep) -> Job:
    """Create a durably queued job from an opaque upload id."""
    # Policy guard before any database interaction: cloud processing requires
    # explicit, affirmative consent. Tier/quota policies are not implemented
    # yet, and no cloud provider call is made anywhere in this path.
    if spec.environment == "cloud" and not spec.cloud_consent:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="cloud processing requires explicit cloud_consent",
        )

    try:
        upload_id = validate_upload_id(spec.file)
    except InvalidUploadIdError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    job_id = uuid.uuid4().hex
    # Request fields without dedicated columns are persisted verbatim as JSON;
    # the jobs.environment foreign key stays NULL until a worker is assigned.
    checkpoint = json.dumps(
        {
            "language": spec.language,
            "environment": spec.environment,
            "preset": spec.preset,
            "mask_region": spec.mask_region.model_dump() if spec.mask_region else None,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    queued_job = QueuedJob(
        id=job_id,
        type=spec.type,
        priority=spec.priority,
        upload_id=upload_id,
        cloud_consent=spec.cloud_consent,
        checkpoint_data=checkpoint,
    )
    try:
        queue.enqueue(queued_job)
        record = queue.fetch_job(job_id)
    except JobAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="a job with this id already exists",
        ) from exc
    except sqlite3.Error as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="job store temporarily unavailable",
        ) from exc
    assert record is not None  # enqueue just inserted this id under our lock
    return _record_to_job(record)


@app.get(
    "/jobs",
    response_model=list[Job],
    dependencies=[Depends(require_bearer)],
)
def list_jobs(
    queue: QueueDep,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[Job]:
    """List jobs in contract order, optionally restricted to one status."""
    if status_filter is not None and status_filter not in JOB_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "unknown status filter; allowed values: "
                + ", ".join(sorted(JOB_STATUSES))
            ),
        )
    try:
        records = queue.list_jobs(status=status_filter, limit=limit)
    except sqlite3.Error as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="job store temporarily unavailable",
        ) from exc
    return [_record_to_job(record) for record in records]


@app.get(
    "/jobs/{job_id}",
    response_model=Job,
    dependencies=[Depends(require_bearer)],
)
def get_job(job_id: str, queue: QueueDep) -> Job:
    """Return the requested job or 404 when the id is unknown."""
    try:
        record = queue.fetch_job(job_id)
    except sqlite3.Error as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="job store temporarily unavailable",
        ) from exc
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="job not found"
        )
    return _record_to_job(record)


@app.get("/health")
async def health(request: Request) -> JSONResponse:
    """Actively probe the durable database; fail clearly without leaking details.

    Returns 200 only when the SQLite store answers a live ``SELECT 1``.
    Any database error maps to a generic 503 body with no driver text,
    SQL or filesystem path. Worker liveness stays ``not_monitored`` until
    the D3 heartbeat protocol exists, so this endpoint never claims a
    component state it cannot prove.
    """
    components = {"database": "ok", "worker": "not_monitored"}
    try:
        request.app.state.queue.ping()
    except sqlite3.Error:
        components["database"] = "error"
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "unhealthy", "components": components},
        )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"status": "healthy", "components": components},
    )
