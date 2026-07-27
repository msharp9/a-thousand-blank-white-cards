"""Full unit tests for engine reducers and _resolve_targets."""

from __future__ import annotations

import pytest

from engine.events import GameEvent, HookContext
from engine.hooks import build_registry, fire_hooks
from engine.reducers import _resolve_card_targets, _resolve_targets, apply_op
from models.effects import (
    AddPointsOp,
    ChangeDrawCountOp,
    CustomNoteOp,
    DestroyCardOp,
    DiscardRandomOp,
    DrawCardsOp,
    EndGameOp,
    ExtraTurnOp,
    ReverseOrderOp,
    RollDieOp,
    ScrambleOrderOp,
    CreateCardOp,
    SetCardAttributeOp,
    SetConditionOp,
    SetPointsOp,
    SetRuleOp,
    SetWinConditionOp,
    SkipTurnOp,
    StealPointsOp,
    SubtractPointsOp,
    TransferCardOp,
)
from models.game_state import GameState, HookSpec, Player


def make_state(players=None, deck=None, turn_order=None, draw_count=1) -> GameState:
    if players is None:
        players = [
            Player(id="p1", name="Alice", score=10, hand=["c1", "c2"]),
            Player(id="p2", name="Bob", score=5, hand=["c3"]),
            Player(id="p3", name="Carol", score=20, hand=[]),
        ]
    return GameState(
        room_code="TEST",
        players=players,
        deck=deck or ["d1", "d2", "d3"],
        turn_order=turn_order or [],
        draw_count=draw_count,
        turn_index=0,
    )


def make_ctx(actor_id="p1", chosen=None) -> HookContext:
    return HookContext(event=GameEvent.ON_PLAY, actor_id=actor_id, chosen_player_id=chosen)


class TestResolveTargets:
    def test_self(self):
        assert _resolve_targets("self", make_ctx("p1"), make_state()) == ["p1"]

    def test_left_neighbor_is_turn_order_successor(self):
        assert _resolve_targets("left_neighbor", make_ctx("p1"), make_state()) == ["p2"]

    def test_right_neighbor_is_turn_order_predecessor(self):
        assert _resolve_targets("right_neighbor", make_ctx("p1"), make_state()) == ["p3"]

    def test_neighbors_from_middle_seat(self):
        assert _resolve_targets("left_neighbor", make_ctx("p2"), make_state()) == ["p3"]
        assert _resolve_targets("right_neighbor", make_ctx("p2"), make_state()) == ["p1"]

    def test_neighbors_follow_reordered_turn_order(self):
        reordered = make_state(turn_order=["p1", "p3", "p2"])
        assert _resolve_targets("left_neighbor", make_ctx("p1"), reordered) == ["p3"]
        assert _resolve_targets("right_neighbor", make_ctx("p1"), reordered) == ["p2"]

    def test_neighbors_two_players_sole_opponent_both_sides(self):
        players = [
            Player(id="p1", name="Alice"),
            Player(id="p2", name="Bob"),
        ]
        state = make_state(players=players)
        assert _resolve_targets("left_neighbor", make_ctx("p1"), state) == ["p2"]
        assert _resolve_targets("right_neighbor", make_ctx("p1"), state) == ["p2"]

    def test_neighbors_single_player_wrap_to_self(self):
        state = make_state(players=[Player(id="p1", name="Alice")])
        assert _resolve_targets("left_neighbor", make_ctx("p1"), state) == ["p1"]
        assert _resolve_targets("right_neighbor", make_ctx("p1"), state) == ["p1"]

    def test_reverse_order_immediately_swaps_neighbors(self):
        state = make_state()
        ctx = make_ctx("p1")
        assert _resolve_targets("left_neighbor", ctx, state) == ["p2"]
        reversed_state = apply_op(state, ReverseOrderOp(), ctx)
        assert _resolve_targets("left_neighbor", ctx, reversed_state) == ["p3"]
        assert _resolve_targets("right_neighbor", ctx, reversed_state) == ["p2"]

    def test_eliminated_player_still_counts_as_a_seat(self):
        players = [
            Player(id="p1", name="Alice"),
            Player(id="p2", name="Bob", eliminated=True),
            Player(id="p3", name="Carol"),
        ]
        state = make_state(players=players)
        assert _resolve_targets("left_neighbor", make_ctx("p1"), state) == ["p2"]
        assert _resolve_targets("right_neighbor", make_ctx("p3"), state) == ["p2"]

    def test_all(self):
        assert set(_resolve_targets("all", make_ctx("p1"), make_state())) == {"p1", "p2", "p3"}

    def test_all_others(self):
        assert set(_resolve_targets("all_others", make_ctx("p1"), make_state())) == {"p2", "p3"}

    def test_chooser_requires_ctx(self):
        with pytest.raises(ValueError):
            _resolve_targets("chooser", make_ctx("p1", chosen=None), make_state())

    def test_chooser_with_ctx(self):
        assert _resolve_targets("chooser", make_ctx("p1", chosen="p2"), make_state()) == ["p2"]

    def test_player_with_most_points(self):
        assert _resolve_targets("player_with_most_points", make_ctx("p1"), make_state()) == ["p3"]

    def test_player_with_least_points(self):
        assert _resolve_targets("player_with_least_points", make_ctx("p1"), make_state()) == ["p2"]

    def test_player_with_empty_hand(self):
        assert _resolve_targets("player_with_empty_hand", make_ctx("p1"), make_state()) == ["p3"]

    def test_unknown_target_raises(self):
        with pytest.raises(ValueError):
            _resolve_targets("not_a_real_target", make_ctx("p1"), make_state())


class TestSkipTurn:
    def test_marks_target_and_leaves_original_unchanged(self):
        state = make_state()
        ctx = make_ctx("p1", chosen="p2")
        new = apply_op(state, SkipTurnOp(target="target_player"), ctx)
        assert new.get_player("p2").conditions == {"skip_next": True}
        assert state.get_player("p2").conditions == {}  # original untouched

    def test_marks_multiple_targets(self):
        state = make_state()
        new = apply_op(state, SkipTurnOp(target="all_others"), make_ctx("p1"))
        assert new.get_player("p2").conditions == {"skip_next": True}
        assert new.get_player("p3").conditions == {"skip_next": True}
        assert state.get_player("p2").conditions == {}
        assert state.get_player("p3").conditions == {}


class TestExtraTurn:
    def test_marks_target_and_leaves_original_unchanged(self):
        state = make_state()
        new = apply_op(state, ExtraTurnOp(target="self"), make_ctx("p1"))
        assert new.get_player("p1").conditions == {"extra_turn": True}
        assert state.get_player("p1").conditions == {}  # original untouched

    def test_marks_multiple_targets(self):
        state = make_state()
        new = apply_op(state, ExtraTurnOp(target="all"), make_ctx("p1"))
        assert new.get_player("p1").conditions == {"extra_turn": True}
        assert new.get_player("p2").conditions == {"extra_turn": True}
        assert new.get_player("p3").conditions == {"extra_turn": True}
        assert state.get_player("p1").conditions == {}


class TestAddPoints:
    def test_adds_to_self(self):
        state = make_state()
        new = apply_op(state, AddPointsOp(amount=5), make_ctx("p1"))
        assert new.get_player("p1").score == 15
        assert state.get_player("p1").score == 10  # immutable

    def test_adds_to_all(self):
        new = apply_op(make_state(), AddPointsOp(target="all", amount=3), make_ctx("p1"))
        assert new.get_player("p1").score == 13
        assert new.get_player("p2").score == 8
        assert new.get_player("p3").score == 23


class TestSubtractPoints:
    def test_subtracts_from_target(self):
        ctx = make_ctx("p1", chosen="p2")
        new = apply_op(make_state(), SubtractPointsOp(target="target_player", amount=3), ctx)
        assert new.get_player("p2").score == 2


class TestSetPoints:
    def test_sets_exact_value(self):
        new = apply_op(make_state(), SetPointsOp(target="self", amount=0), make_ctx("p1"))
        assert new.get_player("p1").score == 0


class TestReverseOrder:
    def test_reverses_default_order(self):
        new = apply_op(make_state(), ReverseOrderOp(), make_ctx("p1"))
        assert new.turn_order == ["p3", "p2", "p1"]

    def test_double_reverse_restores_order(self):
        new = apply_op(make_state(), ReverseOrderOp(), make_ctx("p1"))
        new2 = apply_op(new, ReverseOrderOp(), make_ctx("p1"))
        assert new2.turn_order == ["p1", "p2", "p3"]

    def test_active_player_unaffected(self):
        """Reversing turn_order never moves turn_index — the active player
        (who is not defined by turn_order) stays exactly who it was."""
        state = make_state()
        new = apply_op(state, ReverseOrderOp(), make_ctx("p1"))
        assert new.active_player().id == state.active_player().id


class TestScrambleOrder:
    def test_reorders_turn_order(self):
        import random

        new = apply_op(make_state(), ScrambleOrderOp(), make_ctx("p1"), rng=random.Random(7))
        assert set(new.turn_order) == {"p1", "p2", "p3"}
        assert new.turn_order != ["p1", "p2", "p3"]

    def test_deterministic_given_same_seed(self):
        import random

        first = apply_op(make_state(), ScrambleOrderOp(), make_ctx("p1"), rng=random.Random(7))
        second = apply_op(make_state(), ScrambleOrderOp(), make_ctx("p1"), rng=random.Random(7))
        assert first.turn_order == second.turn_order

    def test_original_state_untouched(self):
        import random

        state = make_state()
        apply_op(state, ScrambleOrderOp(), make_ctx("p1"), rng=random.Random(7))
        assert state.turn_order == []


class TestChangeDrawCount:
    def test_sets_draw_count(self):
        assert apply_op(make_state(), ChangeDrawCountOp(amount=3), make_ctx("p1")).draw_count == 3


class TestStealPoints:
    def test_transfers_points(self):
        ctx = make_ctx("p1", chosen="p2")
        new = apply_op(make_state(), StealPointsOp(from_target="target_player", to_target="self", amount=3), ctx)
        assert new.get_player("p2").score == 2
        assert new.get_player("p1").score == 13

    def test_cannot_steal_below_zero(self):
        ctx = make_ctx("p1", chosen="p2")
        new = apply_op(make_state(), StealPointsOp(from_target="target_player", to_target="self", amount=100), ctx)
        assert new.get_player("p2").score == 0
        assert new.get_player("p1").score == 15  # only stole 5


class TestDrawCards:
    def test_draws_from_deck(self):
        new = apply_op(make_state(deck=["d1", "d2", "d3"]), DrawCardsOp(target="self", amount=2), make_ctx("p1"))
        assert "d1" in new.get_player("p1").hand
        assert "d2" in new.get_player("p1").hand
        assert new.deck == ["d3"]


class TestRollDie:
    def _dice_events(self, state):
        return [e for e in state.history_events if e.kind == "dice_roll"]

    def test_injected_rng_is_deterministic(self):
        import random

        seed_rng = random.Random(42)
        expected = [seed_rng.randint(1, 6) for _ in range(2)]
        op = RollDieOp(sides=6, count=2, outcome="add_points")
        first = apply_op(make_state(), op, make_ctx("p1"), rng=random.Random(42))
        second = apply_op(make_state(), op, make_ctx("p1"), rng=random.Random(42))
        assert self._dice_events(first)[0].data["values"] == expected
        assert first.get_player("p1").score == 10 + sum(expected)
        assert self._dice_events(first)[0].data == self._dice_events(second)[0].data

    def test_pre_resolved_result_replays_without_rng(self):
        new = apply_op(
            make_state(),
            RollDieOp(sides=6, count=2, outcome="add_points", result=[3, 5]),
            make_ctx("p1"),
        )
        assert new.get_player("p1").score == 18  # 10 + 8
        event = self._dice_events(new)[0]
        assert event.data == {"sides": 6, "values": [3, 5], "total": 8}
        assert "Alice rolled 2d6: 3 + 5 = 8" in new.log

    def test_result_value_out_of_bounds_rejected(self):
        with pytest.raises(ValueError):
            RollDieOp(sides=6, count=1, result=[7])
        with pytest.raises(ValueError):
            RollDieOp(sides=6, count=1, result=[0])

    def test_result_length_must_match_count(self):
        with pytest.raises(ValueError):
            RollDieOp(sides=6, count=2, result=[3])

    def test_sides_and_count_bounds(self):
        with pytest.raises(ValueError):
            RollDieOp(sides=1)
        with pytest.raises(ValueError):
            RollDieOp(sides=1001)
        with pytest.raises(ValueError):
            RollDieOp(count=0)
        with pytest.raises(ValueError):
            RollDieOp(count=11)

    def test_outcome_subtract_points(self):
        new = apply_op(
            make_state(),
            RollDieOp(outcome="subtract_points", target="id:p2", result=[4]),
            make_ctx("p1"),
        )
        assert new.get_player("p2").score == 1  # 5 - 4
        assert new.get_player("p1").score == 10

    def test_outcome_draw_cards(self):
        new = apply_op(
            make_state(deck=["d1", "d2", "d3"]),
            RollDieOp(outcome="draw_cards", result=[2]),
            make_ctx("p1"),
        )
        assert new.get_player("p1").hand == ["c1", "c2", "d1", "d2"]
        assert new.deck == ["d3"]

    def test_outcome_none_is_a_bare_roll(self):
        base = make_state()
        new = apply_op(base, RollDieOp(result=[4]), make_ctx("p1"))
        assert [p.score for p in new.players] == [p.score for p in base.players]
        assert [len(p.hand) for p in new.players] == [len(p.hand) for p in base.players]
        event = self._dice_events(new)[0]
        assert event.target_player_ids == []
        assert "Alice rolled 1d6: 4" in new.log

    def test_history_event_shape(self):
        new = apply_op(
            make_state(),
            RollDieOp(sides=20, count=2, outcome="add_points", target="all_others", result=[7, 11]),
            make_ctx("p1", chosen=None),
            rng=None,
        )
        event = self._dice_events(new)[0]
        assert event.kind == "dice_roll"
        assert event.actor_id == "p1"
        assert set(event.target_player_ids) == {"p2", "p3"}
        assert event.amount == 18
        assert event.data == {"sides": 20, "values": [7, 11], "total": 18}

    def test_points_outcome_also_records_score_change(self):
        new = apply_op(make_state(), RollDieOp(outcome="add_points", result=[5]), make_ctx("p1"))
        kinds = [e.kind for e in new.history_events]
        assert kinds.index("dice_roll") < kinds.index("score_change")
        score_event = next(e for e in new.history_events if e.kind == "score_change")
        assert score_event.amount == 5
        assert score_event.target_player_ids == ["p1"]


class TestDiscardRandom:
    def _discard_events(self, state):
        return [e for e in state.history_events if e.kind == "discard"]

    def test_picks_from_target_hand_only(self):
        import random

        base = make_state()
        new = apply_op(base, DiscardRandomOp(target="id:p1", count=1), make_ctx("p1"), rng=random.Random(7))
        (gone,) = [c for c in base.get_player("p1").hand if c not in new.get_player("p1").hand]
        assert gone in {"c1", "c2"}
        assert new.discard == [gone]
        assert new.get_player("p2").hand == ["c3"]
        assert base.get_player("p1").hand == ["c1", "c2"]  # original untouched

    def test_injected_rng_is_deterministic(self):
        import random

        op = DiscardRandomOp(target="self", count=1)
        first = apply_op(make_state(), op, make_ctx("p1"), rng=random.Random(42))
        second = apply_op(make_state(), op, make_ctx("p1"), rng=random.Random(42))
        assert first.discard == second.discard

    def test_count_exceeding_hand_discards_whole_hand(self):
        new = apply_op(make_state(), DiscardRandomOp(target="self", count=10), make_ctx("p1"))
        assert new.get_player("p1").hand == []
        assert set(new.discard) == {"c1", "c2"}
        assert self._discard_events(new)[0].amount == 2

    def test_multiple_targets_each_discard_from_own_hand(self):
        new = apply_op(make_state(), DiscardRandomOp(target="all_others", count=1), make_ctx("p1"))
        assert new.get_player("p1").hand == ["c1", "c2"]
        assert new.get_player("p2").hand == []
        assert new.discard == ["c3"]  # p3's hand was already empty
        assert "[discard_random no-op] Carol has no cards to discard" in new.log

    def test_history_entries_per_target(self):
        new = apply_op(make_state(), DiscardRandomOp(target="id:p2", count=1), make_card_ctx("p1", card_id="src"))
        (event,) = self._discard_events(new)
        assert event.actor_id == "p1"
        assert event.target_player_ids == ["p2"]
        assert event.card_id == "src"
        assert event.amount == 1
        assert event.source == "discard_random"
        assert event.data == {"card_ids": ["c3"]}
        assert "Bob discards 1 random card" in new.log

    def test_count_bounds(self):
        with pytest.raises(ValueError):
            DiscardRandomOp(count=0)
        with pytest.raises(ValueError):
            DiscardRandomOp(count=11)


def make_card_ctx(actor_id="p1", card_id=None, chosen_card_id=None) -> HookContext:
    return HookContext(
        event=GameEvent.ON_PLAY,
        actor_id=actor_id,
        card_id=card_id,
        chosen_card_id=chosen_card_id,
    )


class TestResolveCardTargets:
    def _state_with_zones(self):
        players = [
            Player(id="p1", name="Alice", hand=["h1", "h2"], in_play=["ip1"]),
            Player(id="p2", name="Bob", hand=["h3"], in_play=["ip2", "ip3"]),
        ]
        return GameState(room_code="TEST", players=players, house_rules=["center1"])

    def test_this(self):
        ctx = make_card_ctx("p1", card_id="played")
        assert _resolve_card_targets("this", ctx, self._state_with_zones()) == ["played"]

    def test_this_none_resolves_empty(self):
        ctx = make_card_ctx("p1", card_id=None)
        assert _resolve_card_targets("this", ctx, self._state_with_zones()) == []

    def test_chosen_card_with_ctx(self):
        ctx = make_card_ctx("p1", chosen_card_id="ip2")
        assert _resolve_card_targets("chosen_card", ctx, self._state_with_zones()) == ["ip2"]

    def test_chosen_card_missing_raises(self):
        ctx = make_card_ctx("p1", chosen_card_id=None)
        with pytest.raises(ValueError):
            _resolve_card_targets("chosen_card", ctx, self._state_with_zones())

    def test_all_in_play(self):
        ctx = make_card_ctx("p1")
        assert _resolve_card_targets("all_in_play", ctx, self._state_with_zones()) == ["ip1", "ip2", "ip3"]

    def test_all_in_hand_is_actor_hand(self):
        ctx = make_card_ctx("p1")
        assert _resolve_card_targets("all_in_hand", ctx, self._state_with_zones()) == ["h1", "h2"]

    def test_all_in_center_is_center_zone(self):
        ctx = make_card_ctx("p1")
        assert _resolve_card_targets("all_in_center", ctx, self._state_with_zones()) == ["center1"]

    def test_all_in_center_empty_center_resolves_empty(self):
        ctx = make_card_ctx("p1")
        state = self._state_with_zones().model_copy(update={"house_rules": []})
        assert _resolve_card_targets("all_in_center", ctx, state) == []

    def test_unknown_card_target_raises(self):
        ctx = make_card_ctx("p1")
        with pytest.raises(ValueError):
            _resolve_card_targets("not_a_real_card_target", ctx, self._state_with_zones())

    def _state_with_plays(self, played_ids: list[str], registry: list[str] | None = None):
        from engine.history import append_history_event

        card_ids = registry if registry is not None else played_ids
        state = GameState(
            room_code="TEST",
            players=[Player(id="p1", name="Alice"), Player(id="p2", name="Bob")],
            cards={cid: {"id": cid, "title": cid} for cid in card_ids},
            discard=list(card_ids),
        )
        for cid in played_ids:
            state = append_history_event(state, "play", actor_id="p1", card_id=cid)
        return state

    def test_last_played_resolves_most_recent_play(self):
        state = self._state_with_plays(["older", "newer"])
        ctx = make_card_ctx("p1", card_id="acting")
        assert _resolve_card_targets("last_played", ctx, state) == ["newer"]

    def test_last_played_resolves_an_earlier_completed_play_of_the_same_card(self):
        """A play is recorded only after its effects finish, so the acting
        play is never in history during its own resolution. An EARLIER,
        genuinely completed play of the same card (returned to hand, then
        replayed) is a real prior play and must still resolve — it is not
        filtered out just because its id matches ctx.card_id."""
        state = self._state_with_plays(["acting"])
        ctx = make_card_ctx("p1", card_id="acting")
        assert _resolve_card_targets("last_played", ctx, state) == ["acting"]

    def test_last_played_uses_the_most_recent_completed_play(self):
        """With several plays recorded, the newest surviving one wins, even
        when its id happens to equal the acting card's id."""
        state = self._state_with_plays(["older", "acting"])
        ctx = make_card_ctx("p1", card_id="acting")
        assert _resolve_card_targets("last_played", ctx, state) == ["acting"]

    def test_last_played_empty_history_resolves_empty(self):
        state = self._state_with_plays([])
        ctx = make_card_ctx("p1", card_id="acting")
        assert _resolve_card_targets("last_played", ctx, state) == []

    def test_last_played_skips_cards_no_longer_in_registry(self):
        state = self._state_with_plays(["older", "gone"], registry=["older"])
        ctx = make_card_ctx("p1", card_id="acting")
        assert _resolve_card_targets("last_played", ctx, state) == ["older"]

    def test_last_played_all_plays_gone_resolves_empty(self):
        state = self._state_with_plays(["gone"], registry=[])
        ctx = make_card_ctx("p1", card_id="acting")
        assert _resolve_card_targets("last_played", ctx, state) == []


class TestDestroyCard:
    def test_removes_from_hand(self):
        new = apply_op(make_state(), DestroyCardOp(card_id="c1"), make_ctx("p1"))
        assert "c1" not in new.get_player("p1").hand
        assert "c1" in new.discard

    def test_card_target_this_removes_played_card(self):
        players = [Player(id="p1", name="Alice", in_play=["played"]), Player(id="p2", name="Bob")]
        state = GameState(room_code="TEST", players=players)
        ctx = make_card_ctx("p1", card_id="played")
        new = apply_op(state, DestroyCardOp(card_target="this"), ctx)
        assert "played" not in new.get_player("p1").in_play
        assert "played" in new.discard

    def test_card_target_all_in_play_removes_everywhere(self):
        players = [
            Player(id="p1", name="Alice", in_play=["ip1"]),
            Player(id="p2", name="Bob", in_play=["ip2"]),
        ]
        state = GameState(room_code="TEST", players=players)
        new = apply_op(state, DestroyCardOp(card_target="all_in_play"), make_card_ctx("p1"))
        assert new.get_player("p1").in_play == []
        assert new.get_player("p2").in_play == []
        assert set(new.discard) == {"ip1", "ip2"}

    def test_card_target_chosen_card_from_center(self):
        players = [Player(id="p1", name="Alice")]
        state = GameState(room_code="TEST", players=players, house_rules=["hr1", "hr2"])
        ctx = make_card_ctx("p1", chosen_card_id="hr1")
        new = apply_op(state, DestroyCardOp(card_target="chosen_card"), ctx)
        assert new.house_rules == ["hr2"]
        assert "hr1" in new.discard

    def test_card_target_takes_precedence_over_card_id(self):
        players = [Player(id="p1", name="Alice", hand=["h1"], in_play=["ip1"])]
        state = GameState(room_code="TEST", players=players)
        ctx = make_card_ctx("p1")
        # card_id set to h1, but card_target=all_in_play should win -> ip1 destroyed, h1 kept
        new = apply_op(state, DestroyCardOp(card_id="h1", card_target="all_in_play"), ctx)
        assert new.get_player("p1").hand == ["h1"]
        assert new.get_player("p1").in_play == []
        assert new.discard == ["ip1"]

    def test_no_target_is_noop(self):
        state = make_state()
        new = apply_op(state, DestroyCardOp(), make_card_ctx("p1"))
        assert new.get_player("p1").hand == state.get_player("p1").hand

    def test_no_target_logs_noop_for_visibility(self):
        """A destroy_card resolving to nothing must leave a log trace, not vanish
        silently — otherwise a mis-targeted discard looks like it 'did nothing'."""
        state = make_state()
        new = apply_op(state, DestroyCardOp(), make_card_ctx("p1"))
        assert any("destroy_card no-op" in entry for entry in new.log)

    HOOK_SNIPPET = "def apply(state, ctx):\n    state.add_points('self', 10)\n"

    def _hook(self, source: str, serial: int = 0) -> HookSpec:
        return HookSpec(
            id=f"hook-{source}-{serial}",
            source_card_id=source,
            event=str(GameEvent.ON_TURN_START),
            scope="center",
            code=self.HOOK_SNIPPET,
        )

    def test_destroying_center_card_unregisters_its_hook_and_it_stops_firing(self):
        state = GameState(
            room_code="TEST",
            players=[Player(id="p1", name="Alice", score=0)],
            house_rules=["hr1"],
            hooks=[self._hook("hr1")],
        )
        ctx = HookContext(event=GameEvent.ON_TURN_START, actor_id="p1")
        fired = fire_hooks(state, GameEvent.ON_TURN_START, ctx, registry=build_registry(state))
        assert fired.get_player("p1").score == 10  # hook is live before destroy

        new = apply_op(state, DestroyCardOp(card_id="hr1"), make_card_ctx("p1"))
        assert new.house_rules == []
        assert "hr1" in new.discard
        assert new.hooks == []
        after = fire_hooks(new, GameEvent.ON_TURN_START, ctx, registry=build_registry(new))
        assert after.get_player("p1").score == 0

    def test_destroying_hand_card_without_hooks_leaves_hooks_untouched(self):
        state = make_state().model_copy(update={"hooks": [self._hook("other-card")]})
        new = apply_op(state, DestroyCardOp(card_id="c1"), make_ctx("p1"))
        assert "c1" not in new.get_player("p1").hand
        assert "c1" in new.discard
        assert new.hooks == state.hooks

    def test_card_target_all_in_center_clears_center_zone(self):
        players = [Player(id="p1", name="Alice", hand=["h1"])]
        state = GameState(room_code="TEST", players=players, house_rules=["hr1", "hr2"])
        new = apply_op(state, DestroyCardOp(card_target="all_in_center"), make_card_ctx("p1"))
        assert new.house_rules == []
        assert set(new.discard) == {"hr1", "hr2"}
        assert new.get_player("p1").hand == ["h1"]

    def test_multi_card_destroy_unregisters_all_matching_hooks(self):
        players = [
            Player(id="p1", name="Alice", in_play=["ip1"]),
            Player(id="p2", name="Bob", in_play=["ip2"]),
        ]
        state = GameState(
            room_code="TEST",
            players=players,
            hooks=[self._hook("ip1"), self._hook("ip2"), self._hook("unrelated")],
        )
        new = apply_op(state, DestroyCardOp(card_target="all_in_play"), make_card_ctx("p1"))
        assert set(new.discard) == {"ip1", "ip2"}
        assert [h.source_card_id for h in new.hooks] == ["unrelated"]


class TestTransferCard:
    def test_moves_played_card_from_discard_to_one_hand(self):
        players = [Player(id="p1", name="Alice"), Player(id="p2", name="Bob")]
        state = GameState(
            room_code="TEST",
            players=players,
            cards={"auction": {"id": "auction", "title": "Auction"}},
            discard=["auction"],
        )
        ctx = make_card_ctx("p1", card_id="auction")
        new = apply_op(state, TransferCardOp(card_target="this", to_target="id:p2"), ctx)
        assert new.discard == []
        assert new.get_player("p2").hand == ["auction"]

    def test_requires_exactly_one_recipient(self):
        state = GameState(
            room_code="TEST",
            players=[Player(id="p1", name="Alice"), Player(id="p2", name="Bob")],
            cards={"auction": {"id": "auction", "title": "Auction"}},
            discard=["auction"],
        )
        with pytest.raises(ValueError, match="exactly one"):
            apply_op(
                state,
                TransferCardOp(card_target="this", to_target="all"),
                make_card_ctx("p1", card_id="auction"),
            )


class TestTransferCardOwner:
    """to_target="card_owner" routes each card to its own owner (bead bf3)."""

    def _state(self, *, cards=None, history_plays=(), **overrides) -> GameState:
        from engine.history import append_history_event

        players = overrides.pop(
            "players",
            [Player(id="p1", name="Alice"), Player(id="p2", name="Bob"), Player(id="p3", name="Ivy")],
        )
        state = GameState(room_code="TEST", players=players, cards=cards or {}, **overrides)
        for actor_id, card_id in history_plays:
            state = append_history_event(state, "play", actor_id=actor_id, card_id=card_id)
        return state

    def test_owner_is_current_player_zone_holder(self):
        state = self._state(
            cards={"mod": {"id": "mod", "title": "Modifier"}},
            players=[Player(id="p1", name="Alice"), Player(id="p2", name="Bob", in_play=["mod"])],
        )
        new = apply_op(state, TransferCardOp(card_target="id:mod", to_target="card_owner"), make_card_ctx("p1"))
        assert new.get_player("p2").in_play == []
        assert new.get_player("p2").hand == ["mod"]

    def test_owner_of_discarded_card_is_who_played_it_not_its_creator(self):
        state = self._state(
            cards={"prev": {"id": "prev", "title": "Previous", "creator_id": "p3"}},
            discard=["prev"],
            history_plays=[("p2", "prev")],
        )
        new = apply_op(state, TransferCardOp(card_target="last_played", to_target="card_owner"), make_card_ctx("p1"))
        assert new.discard == []
        assert new.get_player("p2").hand == ["prev"]
        assert new.get_player("p3").hand == []

    def test_owner_falls_back_to_creator_when_never_played(self):
        state = self._state(
            cards={"minted": {"id": "minted", "title": "Minted", "creator_id": "p3"}},
            discard=["minted"],
        )
        new = apply_op(state, TransferCardOp(card_target="id:minted", to_target="card_owner"), make_card_ctx("p1"))
        assert new.discard == []
        assert new.get_player("p3").hand == ["minted"]

    def test_no_resolvable_owner_is_logged_noop(self):
        """A seed card's creator_id is a source label, not a player id."""
        state = self._state(
            cards={"seed": {"id": "seed", "title": "Seed", "creator_id": "seed_corpus"}},
            discard=["seed"],
        )
        new = apply_op(state, TransferCardOp(card_target="id:seed", to_target="card_owner"), make_card_ctx("p1"))
        assert new.discard == ["seed"]
        assert all(p.hand == [] for p in new.players)
        assert any("no resolvable owner for card 'seed'" in entry for entry in new.log)

    def test_no_resolvable_owner_hidden_card_is_not_named(self):
        """An ownerless card in a hidden zone (deck) is logged with a
        placeholder, never its raw id: the shared log is not per-viewer
        redacted, so naming a deck card leaks it to the whole table."""
        state = self._state(
            cards={"hidden1": {"id": "hidden1", "title": "Secret", "attributes": {"color": "red"}}},
            deck=["hidden1"],
        )
        new = apply_op(state, TransferCardOp(card_target="attr:color=red", to_target="card_owner"), make_card_ctx("p1"))
        assert new.deck == ["hidden1"]
        assert any("no resolvable owner for a hidden card" in entry for entry in new.log)
        assert not any("hidden1" in entry for entry in new.log)

    def test_multiple_cards_route_to_their_own_owners(self):
        state = self._state(
            cards={"a": {"id": "a", "title": "A"}, "b": {"id": "b", "title": "B"}},
            players=[
                Player(id="p1", name="Alice", in_play=["a"]),
                Player(id="p2", name="Bob", in_play=["b"]),
            ],
        )
        new = apply_op(state, TransferCardOp(card_target="all_in_play", to_target="card_owner"), make_card_ctx("p1"))
        assert new.get_player("p1").hand == ["a"]
        assert new.get_player("p2").hand == ["b"]

    def test_time_warp_composition_returns_last_played_to_owner(self):
        """The bead-bf3 target composition: transfer_card(last_played, card_owner)
        with the acting card already staged in discard (mid-resolution shape)."""
        state = self._state(
            cards={
                "time-warp": {"id": "time-warp", "title": "Time Warp", "creator_id": "p1"},
                "prev": {"id": "prev", "title": "Previous", "creator_id": "p3"},
            },
            discard=["prev", "time-warp"],
            history_plays=[("p2", "prev")],
        )
        ctx = make_card_ctx("p1", card_id="time-warp")
        new = apply_op(state, TransferCardOp(card_target="last_played", to_target="card_owner"), ctx)
        assert new.get_player("p2").hand == ["prev"]
        assert new.discard == ["time-warp"]


class TestSetWinCondition:
    def test_sets_kind_and_threshold(self):
        new = apply_op(make_state(), SetWinConditionOp(kind="first_to", threshold=50), make_ctx("p1"))
        assert new.win_condition.kind == "first_to"
        assert new.win_condition.threshold == 50


class TestCustomNote:
    def test_appends_log(self):
        new = apply_op(make_state(), CustomNoteOp(note="hello"), make_ctx("p1"))
        assert any("hello" in entry for entry in new.log)


class TestEndGame:
    def test_sets_end_condition_now(self):
        state = make_state()
        assert state.rules.end_condition.type == "deck_empty"
        new = apply_op(state, EndGameOp(), make_ctx("p1"))
        assert new.rules.end_condition.type == "now"
        assert state.rules.end_condition.type == "deck_empty"  # original untouched

    def test_sets_multiple_explicit_winners_in_player_order(self):
        state = make_state()

        new = apply_op(
            state,
            EndGameOp(winners=["id:p2", "id:p1", "id:p2"]),
            make_ctx("p1"),
        )

        assert new.winner_override == ["p1", "p2"]


class TestSetRule:
    def test_sets_scalar_rule(self):
        state = make_state()
        new = apply_op(state, SetRuleOp(path="draw", value=3), make_ctx("p1"))
        assert new.rules.draw == 3
        assert state.rules.draw == 1

    def test_sets_nested_rule_path(self):
        state = make_state()
        new = apply_op(state, SetRuleOp(path="end_condition.type", value="empty_hand"), make_ctx("p1"))
        assert new.rules.end_condition.type == "empty_hand"

    def test_sets_whole_nested_rule(self):
        state = make_state()
        new = apply_op(
            state, SetRuleOp(path="win_condition", value={"kind": "first_to", "threshold": 20}), make_ctx("p1")
        )
        assert new.rules.win_condition.kind == "first_to"
        assert new.rules.win_condition.threshold == 20

    def test_sets_free_form_extra(self):
        state = make_state()
        new = apply_op(state, SetRuleOp(path="extra.color_match", value=True), make_ctx("p1"))
        assert new.rules.extra == {"color_match": True}

    def test_unknown_path_raises(self):
        state = make_state()
        with pytest.raises(ValueError, match="unknown rule path"):
            apply_op(state, SetRuleOp(path="deck", value=[]), make_ctx("p1"))

    def test_invalid_value_raises(self):
        state = make_state()
        with pytest.raises(ValueError, match="invalid value"):
            apply_op(state, SetRuleOp(path="draw", value=-1), make_ctx("p1"))

    def test_change_draw_count_writes_rules(self):
        state = make_state()
        new = apply_op(state, ChangeDrawCountOp(amount=2), make_ctx("p1"))
        assert new.rules.draw == 2
        assert new.draw_count == 2


class TestRuleBindings:
    """set_rule from a known source card links the rule to the card; destroying
    the card reverts the rule with per-path stack semantics."""

    def _center_state(self, *card_ids: str) -> GameState:
        return GameState(
            room_code="TEST",
            players=[Player(id="p1", name="Alice")],
            house_rules=list(card_ids),
        )

    def _set_rule(self, state: GameState, card_id: str, path: str, value) -> GameState:
        return apply_op(state, SetRuleOp(path=path, value=value), make_card_ctx("p1", card_id=card_id))

    def test_set_rule_with_source_card_records_binding(self):
        state = self._set_rule(self._center_state("hr1"), "hr1", "draw", 3)
        assert len(state.rule_bindings) == 1
        binding = state.rule_bindings[0]
        assert binding.source_card_id == "hr1"
        assert binding.path == "draw"
        assert binding.previous_value == 1

    def test_set_rule_without_source_card_records_no_binding(self):
        state = apply_op(make_state(), SetRuleOp(path="draw", value=3), make_ctx("p1"))
        assert state.rule_bindings == []
        assert state.rules.draw == 3

    def test_destroying_rule_card_reverts_its_rule(self):
        state = self._set_rule(self._center_state("hr1"), "hr1", "draw", 3)
        new = apply_op(state, DestroyCardOp(card_id="hr1"), make_card_ctx("p1"))
        assert new.house_rules == []
        assert new.rules.draw == 1
        assert new.rule_bindings == []
        assert any("reverted draw" in entry for entry in new.log)

    def test_destroying_unbound_card_leaves_rule_and_bindings(self):
        state = self._set_rule(self._center_state("hr1", "hr2"), "hr1", "draw", 3)
        new = apply_op(state, DestroyCardOp(card_id="hr2"), make_card_ctx("p1"))
        assert new.rules.draw == 3
        assert len(new.rule_bindings) == 1

    def test_two_cards_same_rule_destroy_newest_then_oldest(self):
        state = self._center_state("hr1", "hr2")
        state = self._set_rule(state, "hr1", "draw", 2)
        state = self._set_rule(state, "hr2", "draw", 3)
        assert state.rules.draw == 3

        after_newest = apply_op(state, DestroyCardOp(card_id="hr2"), make_card_ctx("p1"))
        assert after_newest.rules.draw == 2  # reverts to hr1's rule

        after_both = apply_op(after_newest, DestroyCardOp(card_id="hr1"), make_card_ctx("p1"))
        assert after_both.rules.draw == 1  # back to the default
        assert after_both.rule_bindings == []

    def test_two_cards_same_rule_destroy_oldest_then_newest(self):
        state = self._center_state("hr1", "hr2")
        state = self._set_rule(state, "hr1", "draw", 2)
        state = self._set_rule(state, "hr2", "draw", 3)

        after_oldest = apply_op(state, DestroyCardOp(card_id="hr1"), make_card_ctx("p1"))
        assert after_oldest.rules.draw == 3  # hr2's rule is still the newest write

        after_both = apply_op(after_oldest, DestroyCardOp(card_id="hr2"), make_card_ctx("p1"))
        assert after_both.rules.draw == 1  # skips destroyed hr1's value, back to default
        assert after_both.rule_bindings == []

    def test_nested_rule_path_reverts(self):
        state = self._set_rule(self._center_state("hr1"), "hr1", "win_condition.kind", "lowest_points")
        assert state.rules.win_condition.kind == "lowest_points"
        new = apply_op(state, DestroyCardOp(card_id="hr1"), make_card_ctx("p1"))
        assert new.rules.win_condition.kind == "highest_points"

    def test_bindings_survive_snapshot_round_trip(self):
        state = self._set_rule(self._center_state("hr1"), "hr1", "draw", 3)
        restored = GameState.model_validate(state.model_dump())
        assert [b.source_card_id for b in restored.rule_bindings] == ["hr1"]
        new = apply_op(restored, DestroyCardOp(card_id="hr1"), make_card_ctx("p1"))
        assert new.rules.draw == 1

    def test_bulk_all_in_center_destroy_reverts_rules(self):
        state = self._center_state("hr1", "hr2")
        state = self._set_rule(state, "hr1", "draw", 2)
        state = self._set_rule(state, "hr2", "extra.color_match", True)
        new = apply_op(state, DestroyCardOp(card_target="all_in_center"), make_card_ctx("p1"))
        assert new.house_rules == []
        assert new.rules.draw == 1
        assert new.rules.extra.get("color_match") is None
        assert new.rule_bindings == []

    def test_bulk_destroy_of_stacked_same_rule_reverts_past_both(self):
        state = self._center_state("hr1", "hr2")
        state = self._set_rule(state, "hr1", "draw", 2)
        state = self._set_rule(state, "hr2", "draw", 3)
        new = apply_op(state, DestroyCardOp(card_target="all_in_center"), make_card_ctx("p1"))
        assert new.rules.draw == 1
        assert new.rule_bindings == []

    SET_RULE_SNIPPET = "def apply(state, ctx):\n    state.set_rule('draw', 5)\n"

    def _hook_state(self) -> GameState:
        spec = HookSpec(
            id="hook-hr1-0",
            source_card_id="hr1",
            event=str(GameEvent.ON_TURN_START),
            scope="center",
            code=self.SET_RULE_SNIPPET,
        )
        return self._center_state("hr1").model_copy(update={"hooks": [spec]})

    def test_set_rule_fired_from_hook_binds_to_hook_source_card(self):
        """A hook's set_rule must be attributed to the hook's own card even when
        the triggering event carries no card_id (e.g. turn start), so destroying
        the card still reverts the rule."""
        state = self._hook_state()
        ctx = HookContext(event=GameEvent.ON_TURN_START, actor_id="p1")
        fired = fire_hooks(state, GameEvent.ON_TURN_START, ctx, registry=build_registry(state))
        assert fired.rules.draw == 5
        assert [b.source_card_id for b in fired.rule_bindings] == ["hr1"]

        after = apply_op(fired, DestroyCardOp(card_id="hr1"), make_card_ctx("p1"))
        assert after.rules.draw == 1
        assert after.rule_bindings == []
        assert after.hooks == []

    def test_set_rule_fired_from_hook_ignores_triggering_cards_id(self):
        state = self._hook_state()
        ctx = HookContext(event=GameEvent.ON_TURN_START, actor_id="p1", card_id="someone-elses-card")
        fired = fire_hooks(state, GameEvent.ON_TURN_START, ctx, registry=build_registry(state))
        assert [b.source_card_id for b in fired.rule_bindings] == ["hr1"]


class TestTurnOrderBindings:
    def _state(self) -> GameState:
        return GameState(
            room_code="TEST",
            players=[
                Player(id="p1", name="Alice"),
                Player(id="p2", name="Bob"),
                Player(id="p3", name="Carol"),
            ],
            turn_order=["p1", "p2", "p3"],
            house_rules=["r1", "r2"],
        )

    def test_destroying_reversal_restores_previous_order(self):
        state = apply_op(self._state(), ReverseOrderOp(), make_card_ctx("p1", card_id="r1"))
        assert state.turn_order == ["p3", "p2", "p1"]
        assert [binding.source_card_id for binding in state.turn_order_bindings] == ["r1"]
        restored = apply_op(state, DestroyCardOp(card_id="r1"), make_card_ctx("p1"))
        assert restored.turn_order == ["p1", "p2", "p3"]
        assert restored.turn_order_bindings == []

    def test_buried_reversal_splices_without_changing_live_order(self):
        state = apply_op(self._state(), ReverseOrderOp(), make_card_ctx("p1", card_id="r1"))
        state = apply_op(state, ReverseOrderOp(), make_card_ctx("p1", card_id="r2"))
        after_oldest = apply_op(state, DestroyCardOp(card_id="r1"), make_card_ctx("p1"))
        assert after_oldest.turn_order == ["p1", "p2", "p3"]
        after_both = apply_op(after_oldest, DestroyCardOp(card_id="r2"), make_card_ctx("p1"))
        assert after_both.turn_order == ["p1", "p2", "p3"]

    def test_specialized_rule_ops_bind_when_source_is_on_board(self):
        state = apply_op(self._state(), ChangeDrawCountOp(amount=4), make_card_ctx("p1", card_id="r1"))
        state = apply_op(
            state,
            SetWinConditionOp(kind="lowest_points"),
            make_card_ctx("p1", card_id="r2"),
        )
        assert {(binding.source_card_id, binding.path) for binding in state.rule_bindings} == {
            ("r1", "draw"),
            ("r2", "win_condition"),
        }
        state = apply_op(state, DestroyCardOp(card_id="r2"), make_card_ctx("p1"))
        assert state.rules.win_condition.kind == "highest_points"
        state = apply_op(state, DestroyCardOp(card_id="r1"), make_card_ctx("p1"))
        assert state.rules.draw == 1


class TestOpenTargets:
    def test_id_target_resolves_to_that_player(self):
        state = make_state()
        new = apply_op(state, AddPointsOp(target="id:p2", amount=4), make_ctx("p1"))
        assert new.get_player("p2").score == state.get_player("p2").score + 4

    def test_id_target_of_missing_player_resolves_to_nobody(self):
        state = make_state()
        new = apply_op(state, AddPointsOp(target="id:ghost", amount=4), make_ctx("p1"))
        assert [p.score for p in new.players] == [p.score for p in state.players]

    def test_has_target_resolves_by_condition(self):
        state = make_state().with_condition("p2", "poisoned", 2)
        new = apply_op(state, SubtractPointsOp(target="has:poisoned", amount=3), make_ctx("p1"))
        assert new.get_player("p2").score == state.get_player("p2").score - 3
        assert new.get_player("p1").score == state.get_player("p1").score

    def test_has_target_is_case_insensitive(self):
        state = make_state().with_condition("p2", "Cursed", True)
        new = apply_op(state, SubtractPointsOp(target="has:CURSED", amount=3), make_ctx("p1"))
        assert new.get_player("p2").score == state.get_player("p2").score - 3

    def test_attr_card_target_resolves_matching_cards(self):
        state = make_state()
        cards = {
            "r1": {"id": "r1", "title": "Red", "attributes": {"color": "red"}},
            "b1": {"id": "b1", "title": "Blue", "attributes": {"color": "blue"}},
        }
        players = [p.model_copy(update={"in_play": ["r1", "b1"]}) if p.id == "p1" else p for p in state.players]
        state = state.model_copy(update={"cards": cards, "players": players})
        new = apply_op(state, DestroyCardOp(card_target="attr:color=red"), make_ctx("p1"))
        assert "r1" not in new.get_player("p1").in_play
        assert "b1" in new.get_player("p1").in_play


class TestSetCondition:
    def test_sets_free_form_condition(self):
        state = make_state()
        new = apply_op(state, SetConditionOp(target="id:p2", key="poisoned", value=2), make_ctx("p1"))
        assert new.get_player("p2").conditions == {"poisoned": 2}

    def test_none_value_removes_condition(self):
        state = make_state().with_condition("p1", "poisoned", 1)
        new = apply_op(state, SetConditionOp(target="self", key="poisoned", value=None), make_ctx("p1"))
        assert new.get_player("p1").conditions == {}

    def test_set_and_remove_are_case_insensitive(self):
        state = apply_op(
            make_state(),
            SetConditionOp(target="id:p2", key="Stunned", value=True),
            make_ctx("p1"),
        )
        state = apply_op(
            state,
            SetConditionOp(target="id:p2", key="STUNNED", value=2),
            make_ctx("p1"),
        )
        assert state.get_player("p2").conditions == {"stunned": 2}
        state = apply_op(
            state,
            SetConditionOp(target="id:p2", key="sTuNnEd", value=None),
            make_ctx("p1"),
        )
        assert state.get_player("p2").conditions == {}

    def _bound_state(self, *sources: str) -> GameState:
        players = [
            Player(id="p1", name="Alice"),
            Player(id="p2", name="Bob", in_play=list(sources)),
        ]
        cards = {source: {"id": source, "title": source} for source in sources}
        return GameState(room_code="TEST", players=players, cards=cards)

    def test_board_condition_is_bound_and_destroying_source_clears_it(self):
        state = self._bound_state("curse")
        state = apply_op(
            state,
            SetConditionOp(target="id:p2", key="cursed", value=True),
            make_card_ctx("p1", card_id="curse"),
        )
        assert state.get_player("p2").conditions == {"cursed": True}
        assert [binding.source_card_id for binding in state.condition_bindings] == ["curse"]
        state = apply_op(state, DestroyCardOp(card_id="curse"), make_card_ctx("p1"))
        assert state.get_player("p2").conditions == {}
        assert state.condition_bindings == []

    def test_stacked_condition_restores_previous_source(self):
        state = self._bound_state("mild", "severe")
        state = apply_op(
            state,
            SetConditionOp(target="id:p2", key="poisoned", value=1),
            make_card_ctx("p1", card_id="mild"),
        )
        state = apply_op(
            state,
            SetConditionOp(target="id:p2", key="poisoned", value=3),
            make_card_ctx("p1", card_id="severe"),
        )
        state = apply_op(state, DestroyCardOp(card_id="severe"), make_card_ctx("p1"))
        assert state.get_player("p2").conditions["poisoned"] == 1
        state = apply_op(state, DestroyCardOp(card_id="mild"), make_card_ctx("p1"))
        assert "poisoned" not in state.get_player("p2").conditions

    def test_transfering_source_off_board_retires_condition(self):
        state = self._bound_state("curse")
        state = apply_op(
            state,
            SetConditionOp(target="id:p2", key="cursed", value=True),
            make_card_ctx("p1", card_id="curse"),
        )
        state = apply_op(
            state,
            TransferCardOp(card_target="id:curse", to_target="self"),
            make_card_ctx("p1"),
        )
        assert "curse" in state.get_player("p1").hand
        assert "cursed" not in state.get_player("p2").conditions
        assert state.condition_bindings == []


class TestSetCardAttribute:
    def test_tags_targeted_cards(self):
        state = make_state()
        cards = {"c1": {"id": "c1", "title": "X"}}
        state = state.model_copy(update={"cards": cards})
        new = apply_op(state, SetCardAttributeOp(card_target="id:c1", key="color", value="red"), make_ctx("p1"))
        assert new.cards["c1"]["attributes"] == {"color": "red"}
        assert "attributes" not in state.cards["c1"]


class TestCreateCard:
    def test_creates_into_deck_top_with_compilable_ops(self):
        state = make_state()
        op = CreateCardOp(
            title="Draw 2",
            description="Draw two cards.",
            ops=[{"op": "draw_cards", "args": {"target": "self", "amount": 2}}],
            destination="deck_top",
            count=2,
        )
        new = apply_op(state, op, make_ctx("p1"))
        assert len(new.deck) == len(state.deck) + 2
        created_id = new.deck[0]
        assert new.cards[created_id]["title"] == "Draw 2"
        assert new.cards[created_id]["origin"] == "authored"
        from engine.compile import compile_card

        program = compile_card(new.cards[created_id])
        assert program is not None and program.ops[0].op == "draw_cards"

    def test_creates_into_hand(self):
        state = make_state()
        op = CreateCardOp(title="Gift", destination="hand")
        new = apply_op(state, op, make_ctx("p1"))
        assert any(cid.startswith("created-") for cid in new.get_player("p1").hand)

    def test_creates_into_specific_player_hand(self):
        # Auction case: the actor (p1) mints a card into the winner's (p2) hand.
        state = make_state()
        op = CreateCardOp(title="Double Cat", destination="hand", target="id:p2")
        new = apply_op(state, op, make_ctx("p1"))
        created = [cid for cid in new.get_player("p2").hand if cid.startswith("created-")]
        assert created
        assert not any(cid.startswith("created-") for cid in new.get_player("p1").hand)

    def test_deck_shuffle_is_rng_deterministic(self):
        import random

        state = make_state()
        op = CreateCardOp(title="X", destination="deck_shuffle", count=3)
        a = apply_op(state, op, make_ctx("p1"), rng=random.Random(7))
        b = apply_op(state, op, make_ctx("p1"), rng=random.Random(7))
        assert a.deck == b.deck

    def test_count_capped_at_ten(self):
        with pytest.raises(ValueError):
            CreateCardOp(title="Flood", count=11)
