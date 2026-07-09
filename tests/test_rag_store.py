"""Tests for tbwc.rag.store (embeddings mocked; no real API or network)."""

from __future__ import annotations

from unittest.mock import patch

import pytest


def test_upsert_and_search(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    fake_vector = [0.1] * 1536
    with patch("tbwc.rag.store.embed_text", return_value=fake_vector):
        from tbwc.rag.store import init_store, search, upsert_card

        init_store()
        upsert_card("c1", "Extra Turn", "Take an extra turn.", '{"type":"extra_turn"}', "seed")
        hits = search("take another turn", k=1)
        assert len(hits) == 1
        assert hits[0]["card_id"] == "c1"
        assert hits[0]["source"] == "seed"
        assert "score" in hits[0]


def test_require_client_raises_before_init() -> None:
    import tbwc.rag.store as mod

    mod._client = None
    with pytest.raises(RuntimeError, match="not initialised"):
        mod.search("anything")
