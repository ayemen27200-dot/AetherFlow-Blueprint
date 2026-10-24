"""Smoke tests for importability only; they do not prove processing works."""
from fastapi import FastAPI
from orchestrator.main import app


def test_fastapi_app_is_importable() -> None:
    assert isinstance(app, FastAPI)
    assert any(getattr(route, "path", None) == "/health" for route in app.routes)


def test_health_is_explicitly_degraded_until_components_exist() -> None:
    route = next(route for route in app.routes if getattr(route, "path", None) == "/health")
    # This tests the callable directly and intentionally avoids HTTP client compatibility issues.
    import asyncio
    result = asyncio.run(route.endpoint())
    assert result["status"] == "degraded"
