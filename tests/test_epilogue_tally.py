"""Tests for epilogue vote tallying."""

from __future__ import annotations

from engine.epilogue import CardVotes, tally_votes


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


def test_prior_totals_default_to_zero_when_absent() -> None:
    # No prior_totals arg at all must behave exactly like the old single-game tally.
    votes = {"p1": {"c1": "keep"}, "p2": {"c1": "destroy"}}
    res = tally_votes(votes, ["c1"])
    tally = res.tallies[0]
    assert (tally.keep_votes, tally.destroy_votes) == (1, 1)
    assert res.kept == ["c1"]  # tie -> keep


def test_cumulative_totals_keep_survives_net_negative_this_game() -> None:
    # Game 1 kept a card 6-0; game 2's vote alone is destroy 2-3, but the
    # cumulative total (8-3) still favors keep.
    votes = {
        "p1": {"c1": "keep"},
        "p2": {"c1": "keep"},
        "p3": {"c1": "destroy"},
        "p4": {"c1": "destroy"},
        "p5": {"c1": "destroy"},
    }
    res = tally_votes(votes, ["c1"], prior_totals={"c1": (6, 0)})
    tally = res.tallies[0]
    assert (tally.keep_votes, tally.destroy_votes) == (8, 3)
    assert res.kept == ["c1"]
    assert res.destroyed == []


def test_cumulative_totals_destroy_when_net_negative() -> None:
    # A card that's been getting hammered across games (prior 1-5) picks up
    # another destroy-leaning vote this game and ends up net-negative overall.
    votes = {"p1": {"c1": "destroy"}, "p2": {"c1": "keep"}}
    res = tally_votes(votes, ["c1"], prior_totals={"c1": (1, 5)})
    tally = res.tallies[0]
    assert (tally.keep_votes, tally.destroy_votes) == (2, 6)
    assert res.destroyed == ["c1"]
    assert res.kept == []


def test_cumulative_totals_tie_keeps() -> None:
    res = tally_votes({"p1": {"c1": "destroy"}}, ["c1"], prior_totals={"c1": (3, 2)})
    tally = res.tallies[0]
    assert (tally.keep_votes, tally.destroy_votes) == (3, 3)
    assert res.kept == ["c1"]


def test_prior_totals_only_applied_to_cards_present_in_mapping() -> None:
    # c2 has no prior entry, so it starts fresh at 0-0 even though prior_totals
    # is non-empty for a sibling card.
    res = tally_votes({"p1": {"c2": "keep"}}, ["c1", "c2"], prior_totals={"c1": (0, 4)})
    by_id = {t.card_id: t for t in res.tallies}
    assert (by_id["c1"].keep_votes, by_id["c1"].destroy_votes) == (0, 4)
    assert (by_id["c2"].keep_votes, by_id["c2"].destroy_votes) == (1, 0)
    assert res.destroyed == ["c1"]
    assert res.kept == ["c2"]
