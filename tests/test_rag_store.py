"""Tests for rag.store (embeddings mocked; no real API or network)."""

from __future__ import annotations

from unittest.mock import patch

import pytest


def test_upsert_and_search(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    fake_vector = [0.1] * 1536
    with patch("rag.store.embed_text", return_value=fake_vector):
        from rag.store import init_store, search, upsert_card

        init_store()
        upsert_card("c1", "Extra Turn", "Take an extra turn.", '{"type":"extra_turn"}', "seed")
        hits = search("take another turn", k=1)
        assert len(hits) == 1
        assert hits[0]["card_id"] == "c1"
        assert hits[0]["source"] == "seed"
        assert "score" in hits[0]


def test_list_all_cards_returns_every_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    fake_vector = [0.1] * 1536
    with patch("rag.store.embed_text", return_value=fake_vector):
        from rag.store import init_store, list_all_cards, upsert_card

        init_store()
        upsert_card("c1", "One", "first", "{}", "seed")
        upsert_card("c2", "Two", "second", "{}", "player")
        cards = list_all_cards()
        assert {c["card_id"] for c in cards} == {"c1", "c2"}
        assert {c["source"] for c in cards} == {"seed", "player"}


def test_require_client_raises_before_init() -> None:
    import rag.store as mod

    mod._client = None
    with pytest.raises(RuntimeError, match="not initialised"):
        mod.search("anything")


def test_stable_point_id_is_deterministic() -> None:
    from rag.store import _stable_point_id

    first = _stable_point_id("c1")
    assert first == _stable_point_id("c1")
    assert 0 <= first < 2**63
    assert _stable_point_id("c1") != _stable_point_id("c2")


def test_reupsert_same_card_id_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    fake_vector = [0.1] * 1536
    with patch("rag.store.embed_text", return_value=fake_vector):
        from rag.store import COLLECTION_NAME, init_store, upsert_card

        client = init_store()
        upsert_card("c1", "Extra Turn", "Take an extra turn.", '{"type":"extra_turn"}', "seed")
        upsert_card("c1", "Extra Turn", "Take an extra turn (v2).", '{"type":"extra_turn"}', "seed")
        # Re-seeding the same card_id must overwrite the same point, not duplicate it.
        assert client.count(COLLECTION_NAME).count == 1
