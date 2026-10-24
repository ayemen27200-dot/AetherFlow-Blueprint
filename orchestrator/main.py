"""FastAPI scaffold. Business logic is intentionally incomplete."""

from __future__ import annotations

from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(
    title="AetherFlow Orchestrator",
    version="0.1.0",
    description="Implementation scaffold — job execution endpoints are not finished.",
)


class JobSpec(BaseModel):
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
    mask_region: dict[str, float] | None = None


class Job(BaseModel):
    id: str
    status: str
    type: str
    created_at: str
    output_paths: list[str] = Field(default_factory=list)


@app.post("/jobs", response_model=Job, status_code=202)
async def create_job(spec: JobSpec) -> Job:
    """Create a job after upload/path validation and durable queue insertion are implemented."""
    del spec
    raise HTTPException(status_code=501, detail="Job creation is not implemented in this scaffold")


@app.get("/jobs", response_model=list[Job])
async def list_jobs(status: str | None = None, limit: int = 50) -> list[Job]:
    """List jobs with parameterized filtering and stable pagination (not implemented)."""
    del status, limit
    raise HTTPException(status_code=501, detail="Job listing is not implemented in this scaffold")


@app.get("/jobs/{job_id}", response_model=Job)
async def get_job(job_id: str) -> Job:
    """Return the requested job once repository lookup is implemented."""
    del job_id
    raise HTTPException(status_code=501, detail="Job lookup is not implemented in this scaffold")


@app.get("/health")
async def health() -> dict[str, object]:
    """Report scaffold status; production health must actively check DB and worker state."""
    return {
        "status": "degraded",
        "components": {
            "database": "not_implemented",
            "worker": "not_implemented",
        },
    }
