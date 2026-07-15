"""Smoke tests for the FastAPI app skeleton."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from board.app import EVAL_DRAIN_TIMEOUT_SECONDS, create_app
from config import get_settings
from evals import effect_failure_agent as efa


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    get_settings.cache_clear()
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


def test_startup_warns_without_credentials_but_serves(
    monkeypatch: pytest.MonkeyPatch, tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    """Missing key + blank base_url logs a warning but does NOT hard-fail startup."""
    monkeypatch.chdir(tmp_path)  # isolate from any real .env in the repo root
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    get_settings.cache_clear()
    with caplog.at_level(logging.WARNING):
        with TestClient(create_app()) as c:
            assert c.get("/health").status_code == 200
    assert any("LLM_API_KEY" in rec.message for rec in caplog.records)
    get_settings.cache_clear()


def test_startup_succeeds_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """With a key present, startup completes and the app serves requests."""
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        assert c.get("/health").status_code == 200
    get_settings.cache_clear()


def test_startup_succeeds_with_gateway_base_url(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """A keyless gateway (base_url set, no key) starts without warning or failure."""
    monkeypatch.chdir(tmp_path)  # isolate from any real .env in the repo root
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        assert c.get("/health").status_code == 200
    get_settings.cache_clear()


def test_shutdown_drains_eval_agent_scheduler(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shutdown drains the eval-agent scheduler exactly once, with the bounded timeout."""
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    get_settings.cache_clear()
    mock_scheduler = MagicMock()
    mock_scheduler.drain = AsyncMock()
    monkeypatch.setattr(efa, "get_scheduler", lambda: mock_scheduler)

    with TestClient(create_app()) as c:
        mock_scheduler.drain.assert_not_called()
        assert c.get("/health").status_code == 200

    mock_scheduler.drain.assert_awaited_once_with(timeout=EVAL_DRAIN_TIMEOUT_SECONDS)
    get_settings.cache_clear()
