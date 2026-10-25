"""Structural validation of the published Docker Compose configuration.

The build environment has no Docker daemon, so these tests parse the
Compose YAML directly and assert every security and wiring property the
published stack relies on. A real ``docker compose config`` plus a full
clean-host stack run is executed by the ``compose-smoke`` CI job (the
runner has Docker) through ``scripts/verify_compose_stack.sh``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = REPO_ROOT / "docker" / "docker-compose.yml"
DOCKERFILE_PATH = REPO_ROOT / "docker" / "Dockerfile.orchestrator"


@pytest.fixture(scope="module")
def compose() -> dict:
    with COMPOSE_PATH.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _service(compose: dict, name: str) -> dict:
    services = compose.get("services", {})
    assert name in services, f"service {name!r} must be defined"
    return services[name]


def test_required_services_exist(compose: dict) -> None:
    assert set(compose["services"]) >= {"orchestrator", "worker", "caddy"}


def test_worker_runs_the_cli_against_the_shared_database(compose: dict) -> None:
    worker = _service(compose, "worker")
    command = worker.get("command", [])
    assert command[:3] == ["aetherflow", "worker", "run"]
    # Explicit container paths, not host-relative ./data paths.
    joined = " ".join(command)
    assert "/app/data/aetherflow.db" in joined
    assert "/app/data/upload_store" in joined
    assert "/app/data/outputs" in joined

    env = worker["environment"]
    assert env["AETHERFLOW_DB_PATH"] == "/app/data/aetherflow.db"
    orchestrator = _service(compose, "orchestrator")
    assert (
        orchestrator["environment"]["DATABASE_PATH"]
        == env["AETHERFLOW_DB_PATH"]
    ), "API and worker must open the same physical SQLite file"


def test_both_application_services_share_the_data_volume(compose: dict) -> None:
    expected = "../data:/app/data"
    for name in ("orchestrator", "worker"):
        mounts = {
            source
            for mount in _service(compose, name).get("volumes", [])
            if (source := mount.split(":", 1)[0])
        }
        assert "../data" in mounts
        assert any(str(mount).startswith(expected) for mount in _service(compose, name)["volumes"])


def test_orchestrator_port_is_not_published(compose: dict) -> None:
    orchestrator = _service(compose, "orchestrator")
    assert "ports" not in orchestrator, "Uvicorn must not bind a host port"
    assert "8000" in orchestrator.get("expose", [])
    worker = _service(compose, "worker")
    assert "ports" not in worker, "the worker exposes no network service"
    # Only the reverse proxy publishes host ports.
    caddy = _service(compose, "caddy")
    assert "80:80" in caddy["ports"] and "443:443" in caddy["ports"]
    assert all("8000" not in mapping for mapping in caddy["ports"])


def test_application_services_are_hardened(compose: dict) -> None:
    for name in ("orchestrator", "worker"):
        service = _service(compose, name)
        assert service.get("security_opt") == ["no-new-privileges:true"]
        assert service.get("cap_drop") == ["ALL"]


def test_worker_uses_the_same_image_as_the_orchestrator(compose: dict) -> None:
    orchestrator = _service(compose, "orchestrator")
    worker = _service(compose, "worker")
    assert worker["image"] == orchestrator["image"]
    assert orchestrator["build"]["dockerfile"] == "docker/Dockerfile.orchestrator"


def test_dockerfile_installs_media_tools_and_non_root_user() -> None:
    content = DOCKERFILE_PATH.read_text(encoding="utf-8")
    assert "ffmpeg" in content and "ffprobe" not in content  # ffprobe ships with ffmpeg
    assert "USER aetherflow" in content
    assert "aetherflow worker" not in content  # default command stays the API


def test_env_example_documents_matching_local_paths() -> None:
    env = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "AETHERFLOW_DB_PATH=./data/aetherflow.db" in env
    assert "DATABASE_PATH=./data/aetherflow.db" in env
    # Host-relative equivalents of the container paths used in Compose.
    assert "AETHERFLOW_UPLOAD_STORE=./data/upload_store" in env
    assert "AETHERFLOW_OUTPUT_DIR=./data/outputs" in env
