"""Tests for epilogue vote tallying: strict lifetime verdicts + table favorites."""

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


def test_tie_destroys() -> None:
    votes = {"p1": {"c1": "keep"}, "p2": {"c1": "keep"}, "p3": {"c1": "destroy"}, "p4": {"c1": "destroy"}}
    res = tally_votes(votes, ["c1"])
    assert res.destroyed == ["c1"]
    assert res.kept == []


def test_abstain_not_counted() -> None:
    votes = {"p1": {"c1": "keep"}, "p2": {}}  # p2 abstains
    res = tally_votes(votes, ["c1"])
    tally = res.tallies[0]
    assert tally.keep_votes == 1
    assert tally.destroy_votes == 0
    assert res.kept == ["c1"]


def test_empty_votes_all_destroyed() -> None:
    # Fresh 0-0 is not a strict keep majority.
    res = tally_votes({}, ["c1", "c2"])
    assert res.kept == []
    assert set(res.destroyed) == {"c1", "c2"}
    assert res.favorites == []


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


def test_card_votes_verdict_strict() -> None:
    assert CardVotes(card_id="x", keep_votes=3, destroy_votes=2).verdict() == "keep"
    assert CardVotes(card_id="x", keep_votes=2, destroy_votes=2).verdict() == "destroy"
    assert CardVotes(card_id="x", keep_votes=0, destroy_votes=0).verdict() == "destroy"
    assert CardVotes(card_id="x", keep_votes=1, destroy_votes=2).verdict() == "destroy"


def test_prior_totals_default_to_zero_when_absent() -> None:
    # No prior_totals arg at all must behave exactly like a single-game tally:
    # 1-1 is a cumulative tie, which destroys under the strict rule.
    votes = {"p1": {"c1": "keep"}, "p2": {"c1": "destroy"}}
    res = tally_votes(votes, ["c1"])
    tally = res.tallies[0]
    assert (tally.keep_votes, tally.destroy_votes) == (1, 1)
    assert res.destroyed == ["c1"]


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


def test_cumulative_totals_tie_destroys() -> None:
    res = tally_votes({"p1": {"c1": "destroy"}}, ["c1"], prior_totals={"c1": (3, 2)})
    tally = res.tallies[0]
    assert (tally.keep_votes, tally.destroy_votes) == (3, 3)
    assert res.destroyed == ["c1"]
    assert res.kept == []


def test_prior_totals_only_applied_to_cards_present_in_mapping() -> None:
    # c2 has no prior entry, so it starts fresh at 0-0 even though prior_totals
    # is non-empty for a sibling card.
    res = tally_votes({"p1": {"c2": "keep"}}, ["c1", "c2"], prior_totals={"c1": (0, 4)})
    by_id = {t.card_id: t for t in res.tallies}
    assert (by_id["c1"].keep_votes, by_id["c1"].destroy_votes) == (0, 4)
    assert (by_id["c2"].keep_votes, by_id["c2"].destroy_votes) == (1, 0)
    assert res.destroyed == ["c1"]
    assert res.kept == ["c2"]


def test_outsider_votes_ignored_when_eligible_ids_given() -> None:
    # spec-1 is not eligible; without their destroy votes c1 keeps 1-0.
    votes = {"p1": {"c1": "keep"}, "spec-1": {"c1": "destroy", "c2": "keep"}}
    res = tally_votes(votes, ["c1", "c2"], eligible_player_ids=["p1"])
    by_id = {t.card_id: t for t in res.tallies}
    assert (by_id["c1"].keep_votes, by_id["c1"].destroy_votes) == (1, 0)
    assert (by_id["c2"].keep_votes, by_id["c2"].destroy_votes) == (0, 0)
    assert res.kept == ["c1"]
    assert res.destroyed == ["c2"]
    assert res.favorites == ["c1"]


def test_favorite_is_kept_card_with_most_current_keeps() -> None:
    votes = {
        "p1": {"c1": "keep", "c2": "keep"},
        "p2": {"c1": "keep"},
        "p3": {"c1": "keep", "c2": "keep"},
    }
    res = tally_votes(votes, ["c1", "c2"])
    assert set(res.kept) == {"c1", "c2"}
    assert res.favorites == ["c1"]


def test_favorites_tie_highlights_all_in_card_order() -> None:
    votes = {"p1": {"c2": "keep", "c1": "keep"}, "p2": {"c1": "keep", "c2": "keep"}}
    res = tally_votes(votes, ["c1", "c2"])
    assert res.favorites == ["c1", "c2"]


def test_prior_positive_abstained_card_kept_but_never_favorite() -> None:
    # c-legacy rides prior 5-0 with zero current votes: still kept, but a card
    # with any current keep outranks it, and 0 current keeps can't be favorite.
    votes = {"p1": {"c-new": "keep"}}
    res = tally_votes(votes, ["c-legacy", "c-new"], prior_totals={"c-legacy": (5, 0)})
    assert set(res.kept) == {"c-legacy", "c-new"}
    assert res.favorites == ["c-new"]

    res_abstain_only = tally_votes({}, ["c-legacy"], prior_totals={"c-legacy": (5, 0)})
    assert res_abstain_only.kept == ["c-legacy"]
    assert res_abstain_only.favorites == []


def test_destroyed_card_with_more_current_keeps_never_favorite() -> None:
    # c-doomed draws 3 current keeps but 4 cuts (destroyed); c-modest keeps
    # 2-1. The favorite is the next-highest KEPT card, not the destroyed one.
    votes = {
        "p1": {"c-doomed": "keep", "c-modest": "keep"},
        "p2": {"c-doomed": "keep", "c-modest": "keep"},
        "p3": {"c-doomed": "keep", "c-modest": "destroy"},
        "p4": {"c-doomed": "destroy"},
        "p5": {"c-doomed": "destroy"},
        "p6": {"c-doomed": "destroy"},
        "p7": {"c-doomed": "destroy"},
    }
    res = tally_votes(votes, ["c-doomed", "c-modest"])
    assert res.destroyed == ["c-doomed"]
    assert res.kept == ["c-modest"]
    assert res.favorites == ["c-modest"]


def test_current_cuts_do_not_subtract_from_favorite_rank() -> None:
    # c1 keeps 3-2 (3 current keeps despite the cuts); c2 keeps 2-0. Cuts
    # affect the verdict margin only — c1's 3 keeps still win favorite.
    votes = {
        "p1": {"c1": "keep", "c2": "keep"},
        "p2": {"c1": "keep", "c2": "keep"},
        "p3": {"c1": "keep"},
        "p4": {"c1": "destroy"},
        "p5": {"c1": "destroy"},
    }
    res = tally_votes(votes, ["c1", "c2"])
    assert set(res.kept) == {"c1", "c2"}
    assert res.favorites == ["c1"]
