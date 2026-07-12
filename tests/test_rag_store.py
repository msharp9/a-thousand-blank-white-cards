"""Tests for agent.rag.store (embeddings mocked; no real API or network)."""

from __future__ import annotations

from unittest.mock import patch

import pytest


def test_upsert_and_search(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    fake_vector = [0.1] * 1536
    with (
        patch("agent.rag.store.embed_text", return_value=fake_vector),
        patch("agent.rag.store.embed_text_cached", return_value=fake_vector),
    ):
        from agent.rag.store import init_store, search, upsert_card

        init_store()
        upsert_card("c1", "Extra Turn", "Take an extra turn.", '{"type":"extra_turn"}', "seed")
        hits = search("take another turn", k=1)
        assert len(hits) == 1
        assert hits[0]["card_id"] == "c1"
        assert hits[0]["source"] == "seed"
        assert "score" in hits[0]


def test_list_all_cards_returns_every_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    fake_vector = [0.1] * 1536
    with patch("agent.rag.store.embed_text_cached", return_value=fake_vector):
        from agent.rag.store import init_store, list_all_cards, upsert_card

        init_store()
        upsert_card("c1", "One", "first", "{}", "seed")
        upsert_card("c2", "Two", "second", "{}", "player")
        cards = list_all_cards()
        assert {c["card_id"] for c in cards} == {"c1", "c2"}
        assert {c["source"] for c in cards} == {"seed", "player"}


def test_require_client_raises_before_init() -> None:
    import agent.rag.store as mod

    mod._client = None
    with pytest.raises(RuntimeError, match="not initialised"):
        mod.search("anything")


def test_stable_point_id_is_deterministic() -> None:
    from agent.rag.store import _stable_point_id

    first = _stable_point_id("c1")
    assert first == _stable_point_id("c1")
    assert 0 <= first < 2**63
    assert _stable_point_id("c1") != _stable_point_id("c2")


def test_upsert_rejects_oversized_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """Seed data bypasses the ws schemas, so upsert_card guards the length itself."""
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    from models.card import MAX_CARD_DESCRIPTION, MAX_CARD_TITLE

    fake_vector = [0.1] * 1536
    with patch("agent.rag.store.embed_text", return_value=fake_vector):
        from agent.rag.store import init_store, upsert_card

        init_store()
        with pytest.raises(ValueError, match="exceeds text limits"):
            upsert_card("c1", "x" * (MAX_CARD_TITLE + 1), "ok", "{}", "seed")
        with pytest.raises(ValueError, match="exceeds text limits"):
            upsert_card("c2", "ok", "y" * (MAX_CARD_DESCRIPTION + 1), "{}", "seed")


def test_reupsert_same_card_id_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    fake_vector = [0.1] * 1536
    with patch("agent.rag.store.embed_text_cached", return_value=fake_vector):
        from agent.rag.store import COLLECTION_NAME, init_store, upsert_card

        client = init_store()
        upsert_card("c1", "Extra Turn", "Take an extra turn.", '{"type":"extra_turn"}', "seed")
        upsert_card("c1", "Extra Turn", "Take an extra turn (v2).", '{"type":"extra_turn"}', "seed")
        # Re-seeding the same card_id must overwrite the same point, not duplicate it.
        assert client.count(COLLECTION_NAME).count == 1


def test_upsert_card_defaults_vote_totals_to_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    fake_vector = [0.1] * 1536
    with patch("agent.rag.store.embed_text_cached", return_value=fake_vector):
        from agent.rag.store import get_card_totals, init_store, upsert_card

        init_store()
        upsert_card("c1", "Title", "desc", "{}", "seed")
        assert get_card_totals("c1") == (0, 0)


def test_upsert_card_carries_vote_totals_in_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    fake_vector = [0.1] * 1536
    with patch("agent.rag.store.embed_text_cached", return_value=fake_vector):
        from agent.rag.store import get_card_totals, init_store, list_all_cards, upsert_card

        init_store()
        upsert_card("c1", "Title", "desc", "{}", "player", keep_votes=6, destroy_votes=0)
        assert get_card_totals("c1") == (6, 0)
        cards = list_all_cards()
        assert cards[0]["keep_votes"] == 6
        assert cards[0]["destroy_votes"] == 0


def test_upsert_replaces_prior_vote_totals(monkeypatch: pytest.MonkeyPatch) -> None:
    # Qdrant upsert replaces the whole payload — re-upserting without carrying
    # forward the previous totals would silently reset them to 0.
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    fake_vector = [0.1] * 1536
    with patch("agent.rag.store.embed_text_cached", return_value=fake_vector):
        from agent.rag.store import get_card_totals, init_store, upsert_card

        init_store()
        upsert_card("c1", "Title", "desc", "{}", "player", keep_votes=6, destroy_votes=0)
        prior = get_card_totals("c1")
        assert prior == (6, 0)
        keep, destroy = prior
        upsert_card("c1", "Title", "desc", "{}", "player", keep_votes=keep + 2, destroy_votes=destroy + 3)
        assert get_card_totals("c1") == (8, 3)


def test_get_card_totals_returns_none_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    fake_vector = [0.1] * 1536
    with patch("agent.rag.store.embed_text_cached", return_value=fake_vector):
        from agent.rag.store import get_card_totals, init_store, upsert_card

        init_store()
        upsert_card("c1", "Title", "desc", "{}", "seed")
        assert get_card_totals("never-seeded") is None


def test_delete_card_removes_it_from_the_corpus(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    fake_vector = [0.1] * 1536
    with patch("agent.rag.store.embed_text_cached", return_value=fake_vector):
        from agent.rag.store import delete_card, get_card_totals, init_store, list_all_cards, upsert_card

        init_store()
        upsert_card("c1", "Title", "desc", "{}", "player")
        upsert_card("c2", "Other", "desc", "{}", "player")
        delete_card("c1")
        assert {c["card_id"] for c in list_all_cards()} == {"c2"}
        assert get_card_totals("c1") is None


def test_delete_card_on_missing_id_is_a_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    fake_vector = [0.1] * 1536
    with patch("agent.rag.store.embed_text_cached", return_value=fake_vector):
        from agent.rag.store import delete_card, init_store

        init_store()
        delete_card("never-there")  # must not raise
