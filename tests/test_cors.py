"""Tests that CORS is configured from Settings.cors_origins."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import create_app
from config import Settings, get_settings


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_cors_allows_configured_origin(client: TestClient) -> None:
    origin = get_settings().cors_origins[0]
    resp = client.options(
        "/health",
        headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") in (origin, "*")


def test_cors_origins_env_json_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    # pydantic-settings parses a JSON array env value for list[str]
    monkeypatch.setenv("CORS_ORIGINS", '["https://vercel.app","http://localhost:3000"]')
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert "https://vercel.app" in s.cors_origins
    assert len(s.cors_origins) == 2


def test_cors_default_origins() -> None:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert "http://localhost:3000" in s.cors_origins
