"""Tests for tbwc.rag.seed (store + embeddings mocked)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch


def test_load_seed_cards_upserts_all(tmp_path: Path) -> None:
    sample = [
        {"id": "s1", "title": "A", "description": "Do A", "canonical": "{}"},
        {"title": "B", "description": "Do B"},  # no id, no canonical
        {"title": "C", "description": "Do C", "canonical": {"timing": "immediate"}},  # dict canonical
    ]
    seed_file = tmp_path / "seed_cards.json"
    seed_file.write_text(json.dumps(sample))
    with (
        patch("tbwc.rag.seed.init_store") as mock_init,
        patch("tbwc.rag.seed.upsert_card") as mock_upsert,
    ):
        from tbwc.rag.seed import load_seed_cards

        n = load_seed_cards(seed_file)
    assert n == 3
    mock_init.assert_called_once()
    assert mock_upsert.call_count == 3
    # card 2 got a generated id
    _, kwargs2 = mock_upsert.call_args_list[1]
    assert kwargs2["card_id"] == "seed-001"
    assert kwargs2["canonical"] == ""
    # card 3 dict canonical serialised to JSON string
    _, kwargs3 = mock_upsert.call_args_list[2]
    assert kwargs3["canonical"] == json.dumps({"timing": "immediate"})
    assert kwargs3["source"] == "seed"


def test_missing_file_returns_zero(tmp_path: Path) -> None:
    from tbwc.rag.seed import load_seed_cards

    assert load_seed_cards(tmp_path / "nonexistent.json") == 0


def test_read_seed_cards_assigns_ids(tmp_path: Path) -> None:
    sample = [
        {"id": "keep", "title": "A", "description": "a"},
        {"title": "B", "description": "b"},  # no id -> generated
    ]
    seed_file = tmp_path / "seed_cards.json"
    seed_file.write_text(json.dumps(sample))
    from tbwc.rag.seed import read_seed_cards

    cards = read_seed_cards(seed_file)
    assert cards[0]["id"] == "keep"
    assert cards[1]["id"] == "seed-001"


def test_read_seed_cards_missing_returns_empty(tmp_path: Path) -> None:
    from tbwc.rag.seed import read_seed_cards

    assert read_seed_cards(tmp_path / "nope.json") == []


def test_real_seed_file_shape() -> None:
    # Sanity: the real data file parses and every entry has title+description.
    from tbwc.rag.seed import DEFAULT_SEED_PATH

    data = json.loads(DEFAULT_SEED_PATH.read_text())
    assert len(data) == 60
    assert all("title" in c and "description" in c for c in data)
