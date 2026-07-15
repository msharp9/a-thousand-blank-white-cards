"""Tests for the BM25 + dense hybrid retriever and its RRF fusion helper."""

from __future__ import annotations

from unittest.mock import patch

from agent.rag.retrievers import _rrf, _tokenize, hybrid_retriever


def test_tokenize_lowercases_and_strips_punctuation() -> None:
    assert _tokenize("Zebra-Herd, Vol. 2!") == ["zebra", "herd", "vol", "2"]


def test_tokenize_keeps_alphanumerics_only() -> None:
    assert _tokenize("  a1  b_2 -- c3  ") == ["a1", "b", "2", "c3"]


def test_rrf_orders_by_fused_score_and_dedups() -> None:
    list_a = [{"card_id": "a", "title": "A"}, {"card_id": "b", "title": "B"}]
    list_b = [{"card_id": "b", "title": "B"}, {"card_id": "c", "title": "C"}]

    out = _rrf([list_a, list_b], limit=10)
    ids = [d["card_id"] for d in out]

    assert ids == ["b", "a", "c"]


def test_rrf_fused_score_math_for_doc_in_both_lists() -> None:
    list_a = [{"card_id": "a"}, {"card_id": "b"}]
    list_b = [{"card_id": "b"}, {"card_id": "a"}]

    out = _rrf([list_a, list_b], limit=10, rrf_constant=60)
    scores = {d["card_id"]: d["score"] for d in out}

    expected_a = 1.0 / 61 + 1.0 / 62
    expected_b = 1.0 / 62 + 1.0 / 61
    assert scores["a"] == expected_a
    assert scores["b"] == expected_b


def test_rrf_respects_limit() -> None:
    list_a = [{"card_id": str(i)} for i in range(5)]
    out = _rrf([list_a], limit=2)
    assert len(out) == 2
    assert [d["card_id"] for d in out] == ["0", "1"]


def test_rrf_does_not_mutate_inputs() -> None:
    doc = {"card_id": "a", "score": 0.5}
    list_a = [doc]

    out = _rrf([list_a], limit=10)

    assert doc["score"] == 0.5
    assert out[0] is not doc
    assert out[0]["score"] != 0.5


def test_rrf_falls_back_to_title_key_when_no_card_id() -> None:
    list_a = [{"title": "Only Title"}]
    list_b = [{"title": "Only Title"}]

    out = _rrf([list_a, list_b], limit=10)

    assert len(out) == 1
    assert out[0]["title"] == "Only Title"


def test_hybrid_retriever_surfaces_rare_keyword_card_missed_by_dense() -> None:
    # rare-1 ranks low in dense (BM25-invisible embedding neighborhood) but is
    # the only card containing the query's keywords, so it should win BM25
    # rank 1 and, combined with its (low) dense rank, out-fuse the purely
    # dense-ranked common cards.
    common_1 = {"card_id": "common-1", "title": "Common One", "description": "generic filler card"}
    common_2 = {"card_id": "common-2", "title": "Common Two", "description": "another generic card"}
    rare_1 = {
        "card_id": "rare-1",
        "title": "Xylophone Marmoset",
        "description": "a xylophone playing marmoset",
    }
    dense_hits = [common_1, common_2, rare_1]
    all_cards = [common_1, common_2, rare_1]

    with (
        patch("agent.rag.retrievers.store.search", return_value=dense_hits) as mock_search,
        patch("agent.rag.retrievers.store.list_all_cards", return_value=all_cards) as mock_list_all,
    ):
        retrieve = hybrid_retriever()
        results = retrieve("xylophone marmoset", k=4)

    mock_search.assert_called_once_with("xylophone marmoset", k=10)
    mock_list_all.assert_called_once_with()
    ids = [d["card_id"] for d in results]
    assert "rare-1" in ids
    assert ids[0] == "rare-1"


def test_hybrid_retriever_empty_corpus_returns_dense_only_trimmed_to_k() -> None:
    dense_hits = [{"card_id": str(i), "title": f"Card {i}"} for i in range(6)]

    with (
        patch("agent.rag.retrievers.store.search", return_value=dense_hits),
        patch("agent.rag.retrievers.store.list_all_cards", return_value=[]),
    ):
        retrieve = hybrid_retriever()
        results = retrieve("anything", k=3)

    assert results == dense_hits[:3]


def test_hybrid_retriever_query_with_no_tokens_returns_dense_only() -> None:
    dense_hits = [{"card_id": "a", "title": "A"}]
    all_cards = [{"card_id": "b", "title": "B", "description": "some words here"}]

    with (
        patch("agent.rag.retrievers.store.search", return_value=dense_hits),
        patch("agent.rag.retrievers.store.list_all_cards", return_value=all_cards),
    ):
        retrieve = hybrid_retriever()
        results = retrieve("!!! ---", k=4)

    assert results == dense_hits[:4]


def test_hybrid_retriever_excludes_zero_bm25_score_cards() -> None:
    # A third, unrelated distractor keeps "dragon" a minority term across the
    # corpus so BM25Okapi's idf term stays positive for the matching card.
    dense_hits: list[dict] = []
    all_cards = [
        {"card_id": "match", "title": "Dragon Fire", "description": "a fire breathing dragon"},
        {"card_id": "no-match", "title": "Umbrella Stand", "description": "holds umbrellas"},
        {"card_id": "distractor", "title": "Garden Hose", "description": "waters the garden"},
    ]

    with (
        patch("agent.rag.retrievers.store.search", return_value=dense_hits),
        patch("agent.rag.retrievers.store.list_all_cards", return_value=all_cards),
    ):
        retrieve = hybrid_retriever()
        results = retrieve("dragon", k=4)

    ids = [d["card_id"] for d in results]
    assert ids == ["match"]
