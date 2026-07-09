"""Tests for epilogue vote tallying."""

from __future__ import annotations

from tbwc.engine.epilogue import CardVotes, tally_votes


def test_unanimous_keep() -> None:
    votes = {"p1": {"c1": "keep"}, "p2": {"c1": "keep"}}
    res = tally_votes(votes, ["c1"])
    assert res.kept == ["c1"]
    assert res.destroyed == []


def test_unanimous_destroy() -> None:
    votes = {"p1": {"c1": "destroy"}, "p2": {"c1": "destroy"}}
    res = tally_votes(votes, ["c1"])
    assert res.destroyed == ["c1"]
    assert res.kept == []


def test_tie_defaults_to_keep() -> None:
    votes = {"p1": {"c1": "keep"}, "p2": {"c1": "keep"}, "p3": {"c1": "destroy"}, "p4": {"c1": "destroy"}}
    res = tally_votes(votes, ["c1"])
    assert res.kept == ["c1"]


def test_abstain_not_counted() -> None:
    votes = {"p1": {"c1": "keep"}, "p2": {}}  # p2 abstains
    res = tally_votes(votes, ["c1"])
    tally = res.tallies[0]
    assert tally.keep_votes == 1
    assert tally.destroy_votes == 0
    assert res.kept == ["c1"]


def test_empty_votes_all_kept() -> None:
    res = tally_votes({}, ["c1", "c2"])
    assert set(res.kept) == {"c1", "c2"}
    assert res.destroyed == []


def test_unknown_card_votes_ignored() -> None:
    votes = {"p1": {"cX": "destroy", "c1": "keep"}}
    res = tally_votes(votes, ["c1"])
    assert res.kept == ["c1"]
    assert len(res.tallies) == 1


def test_mixed_cards() -> None:
    votes = {
        "p1": {"c1": "keep", "c2": "destroy"},
        "p2": {"c1": "keep", "c2": "destroy"},
        "p3": {"c1": "destroy", "c2": "destroy"},
    }
    res = tally_votes(votes, ["c1", "c2"])
    assert res.kept == ["c1"]
    assert res.destroyed == ["c2"]


def test_card_votes_verdict_tie() -> None:
    assert CardVotes(card_id="x", keep_votes=2, destroy_votes=2).verdict() == "keep"
    assert CardVotes(card_id="x", keep_votes=1, destroy_votes=2).verdict() == "destroy"
