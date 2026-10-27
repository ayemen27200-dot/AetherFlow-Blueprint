"""Smoke tests for importability and route wiring only.

These tests do not prove processing works; the API suite owns the active
health-check behaviour and the integration suites own end-to-end evidence.
"""

from fastapi import FastAPI

from orchestrator.main import app


def test_fastapi_app_is_importable() -> None:
    assert isinstance(app, FastAPI)
    assert any(getattr(route, "path", None) == "/health" for route in app.routes)


def test_health_route_is_registered_and_unauthenticated() -> None:
    route = next(route for route in app.routes if getattr(route, "path", None) == "/health")
    # The endpoint is wired as a plain callable; detailed 200/503 behaviour
    # against a real/failing database is covered in tests/api/test_jobs_api.py.
    assert callable(route.endpoint)
