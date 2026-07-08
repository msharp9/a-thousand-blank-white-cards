"""Smoke tests for the FastAPI app skeleton."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tbwc.app import create_app
from tbwc.config import get_settings


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_cors_headers_present(client: TestClient) -> None:
    """CORS preflight should include allowed origins."""
    settings = get_settings()
    origin = settings.cors_origins[0]
    response = client.options(
        "/health",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") in (origin, "*")
