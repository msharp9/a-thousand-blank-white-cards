"""Tests for the mtg_lookup Scryfall tool (network mocked — NO live requests)."""

from __future__ import annotations

import httpx
import pytest

from agent.tools import mtg_lookup as mod


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralise the rate-limit sleep so tests stay fast."""
    monkeypatch.setattr(mod.time, "sleep", lambda *_a, **_k: None)


class _FakeResponse:
    """Minimal stand-in for httpx.Response used by the fake GET."""

    def __init__(self, *, json_data: dict | None = None, status_code: int = 200) -> None:
        self._json = json_data or {}
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"status {self.status_code}",
                request=httpx.Request("GET", mod._SCRYFALL_NAMED_URL),
                response=httpx.Response(self.status_code),
            )

    def json(self) -> dict:
        return self._json


_LIGHTNING_BOLT = {
    "name": "Lightning Bolt",
    "mana_cost": "{R}",
    "type_line": "Instant",
    "oracle_text": "Lightning Bolt deals 3 damage to any target.",
}


def test_successful_lookup_summarizes_card(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(*_a, **_k):
        return _FakeResponse(json_data=_LIGHTNING_BOLT)

    monkeypatch.setattr(mod.httpx, "get", fake_get)

    result = mod.mtg_lookup.invoke({"card_name": "lightning bolt"})

    assert "Lightning Bolt" in result
    assert "Instant" in result
    assert "{R}" in result
    assert "Lightning Bolt deals 3 damage to any target." in result


def test_get_sends_useragent_and_fuzzy_param(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake_get(url, *, params=None, headers=None, timeout=None):  # noqa: ANN001
        captured["url"] = url
        captured["params"] = params
        captured["headers"] = headers
        return _FakeResponse(json_data=_LIGHTNING_BOLT)

    monkeypatch.setattr(mod.httpx, "get", fake_get)

    mod.mtg_lookup.invoke({"card_name": "lightning bolt"})

    assert captured["url"] == mod._SCRYFALL_NAMED_URL
    assert captured["params"] == {"fuzzy": "lightning bolt"}
    assert "User-Agent" in captured["headers"]
    assert captured["headers"]["Accept"] == "application/json"


def test_long_oracle_text_is_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    long_card = dict(_LIGHTNING_BOLT, oracle_text="x" * (mod._MAX_ORACLE_CHARS + 200))
    monkeypatch.setattr(mod.httpx, "get", lambda *a, **k: _FakeResponse(json_data=long_card))

    result = mod.mtg_lookup.invoke({"card_name": "big"})

    assert result.endswith("…")
    # Header line + truncated body + ellipsis, well under the raw oracle length.
    assert len(result) < mod._MAX_ORACLE_CHARS + 100


def test_404_returns_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod.httpx, "get", lambda *a, **k: _FakeResponse(status_code=404))

    result = mod.mtg_lookup.invoke({"card_name": "definitely not a card"})

    assert result == "no MTG card found for 'definitely not a card'"


def test_network_error_returns_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a, **_k):
        raise httpx.ConnectError("network down")

    monkeypatch.setattr(mod.httpx, "get", boom)

    result = mod.mtg_lookup.invoke({"card_name": "lightning bolt"})

    assert result == "MTG lookup unavailable"


def test_non_404_http_error_returns_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod.httpx, "get", lambda *a, **k: _FakeResponse(status_code=500))

    result = mod.mtg_lookup.invoke({"card_name": "lightning bolt"})

    assert result == "MTG lookup unavailable"


def test_malformed_payload_returns_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    class BadJson(_FakeResponse):
        def json(self) -> dict:
            raise ValueError("not json")

    monkeypatch.setattr(mod.httpx, "get", lambda *a, **k: BadJson())

    result = mod.mtg_lookup.invoke({"card_name": "lightning bolt"})

    assert result == "MTG lookup unavailable"


def test_empty_name_returns_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    # No GET should be issued for an empty name.
    def fail_get(*_a, **_k):  # pragma: no cover - must not be called
        raise AssertionError("network should not be hit for empty name")

    monkeypatch.setattr(mod.httpx, "get", fail_get)

    assert mod.mtg_lookup.invoke({"card_name": "   "}) == "no MTG card found for ''"


def test_tool_metadata() -> None:
    tool_obj = mod.get_mtg_lookup_tool()
    assert tool_obj.name == "mtg_lookup"
    assert bool(tool_obj.description)
    assert tool_obj is mod.mtg_lookup
