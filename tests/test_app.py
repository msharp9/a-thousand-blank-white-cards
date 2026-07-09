"""Smoke tests for the FastAPI app skeleton."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tbwc.app import create_app
from tbwc.config import OPENAI_API_KEY_ERROR, get_settings


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Startup fails fast without a key; provide one so smoke tests can run.
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
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


def test_startup_fails_without_openai_key(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Startup (lifespan) fails fast with a clear message when the key is unset."""
    monkeypatch.chdir(tmp_path)  # isolate from any real .env in the repo root
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    get_settings.cache_clear()
    app = create_app()
    with pytest.raises(RuntimeError) as exc:
        # Entering the context manager runs the lifespan startup.
        with TestClient(app):
            pass
    assert str(exc.value) == OPENAI_API_KEY_ERROR
    get_settings.cache_clear()


def test_startup_succeeds_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """With a key present, startup completes and the app serves requests."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        assert c.get("/health").status_code == 200
    get_settings.cache_clear()
