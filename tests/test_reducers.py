"""Full unit tests for engine reducers and _resolve_targets."""

from __future__ import annotations

import pytest

from engine.events import GameEvent, HookContext
from engine.reducers import _resolve_card_targets, _resolve_targets, apply_op
from models.effects import (
    AddPointsOp,
    ChangeDrawCountOp,
    CustomNoteOp,
    DestroyCardOp,
    DrawCardsOp,
    EndGameOp,
    ExtraTurnOp,
    ReverseOrderOp,
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
from models.game_state import GameState, Player


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

    def test_right_neighbor_default_order(self):
        assert _resolve_targets("right_neighbor", make_ctx("p1"), make_state()) == ["p2"]

    def test_left_neighbor_default_order(self):
        assert _resolve_targets("left_neighbor", make_ctx("p1"), make_state()) == ["p3"]

    def test_right_neighbor_reversed_order(self):
        reversed_order = make_state(turn_order=["p1", "p3", "p2"])
        assert _resolve_targets("right_neighbor", make_ctx("p1"), reversed_order) == ["p3"]

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

    def test_unknown_card_target_raises(self):
        ctx = make_card_ctx("p1")
        with pytest.raises(ValueError):
            _resolve_card_targets("not_a_real_card_target", ctx, self._state_with_zones())


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
