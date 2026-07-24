"""move_cards + shuffle_deck ops: models, reducers, compile, sandbox mirrors (b8x.2)."""

from __future__ import annotations

import random

import pytest
from pydantic import ValidationError

from engine.compile import compile_card
from engine.events import GameEvent, HookContext
from engine.reducers import apply_op
from engine.sandbox.api_surface import SandboxGame
from engine.sandbox.revalidate import DiffValidationError, apply_snippet_diff, parse_diff
from models.effects import CreateCardOp, MoveCardsOp, SetRuleOp, ShuffleDeckOp, op_requires_choice
from models.game_state import GameState, HookSpec, Player


def make_state(**overrides) -> GameState:
    players = overrides.pop(
        "players",
        [
            Player(id="p1", name="Alice", hand=["c1", "c2"]),
            Player(id="p2", name="Bob", hand=["c3", "c4", "c5"], in_play=["ip1"]),
        ],
    )
    defaults = dict(room_code="TEST", players=players, deck=["d1", "d2", "d3"], turn_index=0)
    defaults.update(overrides)
    return GameState(**defaults)


def make_ctx(actor_id="p1", card_id=None, chosen_card_id=None) -> HookContext:
    return HookContext(event=GameEvent.ON_PLAY, actor_id=actor_id, card_id=card_id, chosen_card_id=chosen_card_id)


class TestMoveCardsModel:
    def test_requires_exactly_one_source(self):
        with pytest.raises(ValidationError, match="exactly one of card_target or from_zone"):
            MoveCardsOp(to_zone="discard")
        with pytest.raises(ValidationError, match="exactly one of card_target or from_zone"):
            MoveCardsOp(card_target="this", from_zone="deck", to_zone="discard")

    def test_from_player_required_exactly_for_player_zones(self):
        with pytest.raises(ValidationError, match="requires from_player"):
            MoveCardsOp(from_zone="hand", to_zone="discard")
        with pytest.raises(ValidationError, match="from_player is only valid"):
            MoveCardsOp(from_zone="deck", from_player="self", to_zone="discard")
        op = MoveCardsOp(from_zone="in_play", from_player="id:p2", to_zone="discard")
        assert op.from_player == "id:p2"

    def test_to_player_required_exactly_for_player_zones(self):
        with pytest.raises(ValidationError, match="requires to_player"):
            MoveCardsOp(from_zone="deck", to_zone="hand")
        with pytest.raises(ValidationError, match="to_player is only valid"):
            MoveCardsOp(from_zone="deck", to_zone="discard", to_player="self")
        op = MoveCardsOp(from_zone="deck", to_zone="hand", to_player="self")
        assert op.to_player == "self"

    def test_count_bounds(self):
        with pytest.raises(ValidationError):
            MoveCardsOp(from_zone="deck", to_zone="discard", count=0)
        with pytest.raises(ValidationError):
            MoveCardsOp(from_zone="deck", to_zone="discard", count=51)

    def test_choice_targets_flag_requires_choice(self):
        assert op_requires_choice(MoveCardsOp(card_target="chosen_card", to_zone="discard"))
        assert op_requires_choice(MoveCardsOp(from_zone="hand", from_player="chooser", to_zone="discard"))
        assert op_requires_choice(MoveCardsOp(from_zone="deck", to_zone="hand", to_player="chooser"))
        assert not op_requires_choice(MoveCardsOp(from_zone="deck", to_zone="discard"))


class TestMoveCardsReducer:
    def test_mill_top_n_deck_to_discard(self):
        new = apply_op(
            make_state(),
            MoveCardsOp(from_zone="deck", selector="top", count=2, to_zone="discard"),
            make_ctx(card_id="src"),
        )
        assert new.deck == ["d3"]
        assert new.discard == ["d1", "d2"]
        (event,) = [e for e in new.history_events if e.kind == "discard"]
        assert event.source == "move_cards"
        assert event.amount == 2
        assert event.card_id == "src"
        assert event.data == {"card_ids": ["d1", "d2"]}
        assert "[move_cards] 2 cards: deck -> discard" in new.log

    def test_mill_all(self):
        new = apply_op(make_state(), MoveCardsOp(from_zone="deck", selector="all", to_zone="discard"), make_ctx())
        assert new.deck == []
        assert new.discard == ["d1", "d2", "d3"]

    def test_count_exceeding_zone_moves_what_is_there(self):
        new = apply_op(
            make_state(), MoveCardsOp(from_zone="deck", selector="top", count=50, to_zone="discard"), make_ctx()
        )
        assert new.deck == []
        assert len(new.discard) == 3

    def test_draw_top_of_discard_into_hand(self):
        state = make_state(discard=["x", "y"])
        new = apply_op(
            state, MoveCardsOp(from_zone="discard", selector="top", to_zone="hand", to_player="self"), make_ctx("p1")
        )
        assert new.get_player("p1").hand == ["c1", "c2", "y"]
        assert new.discard == ["x"]

    def test_discard_to_center(self):
        state = make_state(discard=["x", "y"])
        new = apply_op(state, MoveCardsOp(from_zone="discard", selector="top", to_zone="center"), make_ctx())
        assert new.house_rules == ["y"]
        assert new.discard == ["x"]

    def test_discard_to_in_play(self):
        state = make_state(discard=["x"])
        new = apply_op(
            state,
            MoveCardsOp(from_zone="discard", selector="top", to_zone="in_play", to_player="id:p2"),
            make_ctx(),
        )
        assert new.get_player("p2").in_play == ["ip1", "x"]
        assert new.discard == []

    def test_remove_deck_from_game(self):
        new = apply_op(make_state(), MoveCardsOp(from_zone="deck", selector="all", to_zone="exile"), make_ctx())
        assert new.deck == []
        assert new.exiled == ["d1", "d2", "d3"]

    def test_return_card_to_deck_bottom(self):
        state = make_state(cards={"c2": {"id": "c2", "title": "Two"}})
        new = apply_op(state, MoveCardsOp(card_target="id:c2", to_zone="deck", to_position="bottom"), make_ctx("p1"))
        assert new.deck == ["d1", "d2", "d3", "c2"]
        assert new.get_player("p1").hand == ["c1"]

    def test_move_to_deck_top_preserves_selection_order(self):
        state = make_state(discard=["x", "y"])
        new = apply_op(
            state, MoveCardsOp(from_zone="discard", selector="all", to_zone="deck", to_position="top"), make_ctx()
        )
        assert new.deck == ["x", "y", "d1", "d2", "d3"]
        assert new.discard == []

    def test_move_bottom_of_deck_to_top(self):
        new = apply_op(
            make_state(),
            MoveCardsOp(from_zone="deck", selector="bottom", to_zone="deck", to_position="top"),
            make_ctx(),
        )
        assert new.deck == ["d3", "d1", "d2"]

    def test_shuffle_position_uses_injected_rng(self):
        op = MoveCardsOp(card_target="id:c1", to_zone="deck", to_position="shuffle")
        state = make_state(cards={"c1": {"id": "c1", "title": "One"}}, deck=[f"d{i}" for i in range(20)])
        first = apply_op(state, op, make_ctx("p1"), rng=random.Random(3))
        second = apply_op(state, op, make_ctx("p1"), rng=random.Random(3))
        assert first.deck == second.deck
        assert "c1" in first.deck
        assert first.get_player("p1").hand == ["c2"]

    def test_random_selector_uses_injected_rng_from_other_hand(self):
        op = MoveCardsOp(
            from_zone="hand", from_player="id:p2", selector="random", count=2, to_zone="hand", to_player="self"
        )
        first = apply_op(make_state(), op, make_ctx("p1"), rng=random.Random(11))
        second = apply_op(make_state(), op, make_ctx("p1"), rng=random.Random(11))
        assert first.get_player("p1").hand == second.get_player("p1").hand
        moved = set(first.get_player("p1").hand) - {"c1", "c2"}
        assert len(moved) == 2
        assert moved <= {"c3", "c4", "c5"}
        assert set(first.get_player("p2").hand) == {"c3", "c4", "c5"} - moved

    def test_from_player_multi_target_takes_from_each_hand(self):
        new = apply_op(
            make_state(),
            MoveCardsOp(from_zone="hand", from_player="all", selector="top", to_zone="center"),
            make_ctx("p1"),
        )
        assert new.house_rules == ["c2", "c5"]

    def test_empty_source_is_logged_no_op(self):
        state = make_state(discard=[])
        new = apply_op(
            state, MoveCardsOp(from_zone="discard", selector="top", to_zone="hand", to_player="self"), make_ctx()
        )
        assert new.discard == []
        assert new.get_player("p1").hand == ["c1", "c2"]
        assert any("[move_cards no-op] no cards to move from 'discard'" in line for line in new.log)

    def test_unresolved_recipient_is_logged_no_op(self):
        new = apply_op(
            make_state(),
            MoveCardsOp(from_zone="deck", selector="top", to_zone="hand", to_player="has:nothing"),
            make_ctx(),
        )
        assert new.deck == ["d1", "d2", "d3"]
        assert any("[move_cards no-op] resolved no players" in line for line in new.log)

    def test_multiple_recipients_raise(self):
        with pytest.raises(ValueError, match="exactly one destination player"):
            apply_op(
                make_state(),
                MoveCardsOp(from_zone="deck", selector="top", to_zone="hand", to_player="all"),
                make_ctx(),
            )

    def test_card_target_moves_from_wherever_cards_live(self):
        state = make_state(
            house_rules=["hr1", "hr2"],
            cards={"hr1": {"id": "hr1"}, "hr2": {"id": "hr2"}},
        )
        new = apply_op(state, MoveCardsOp(card_target="all_in_center", to_zone="exile"), make_ctx())
        assert new.house_rules == []
        assert new.exiled == ["hr1", "hr2"]

    def test_log_never_names_hidden_cards(self):
        new = apply_op(
            make_state(),
            MoveCardsOp(from_zone="hand", from_player="id:p2", selector="random", to_zone="hand", to_player="self"),
            make_ctx("p1"),
            rng=random.Random(0),
        )
        move_lines = [line for line in new.log if "move_cards" in line]
        assert move_lines
        for cid in ("c3", "c4", "c5"):
            assert all(cid not in line for line in move_lines)
        assert not [e for e in new.history_events if e.kind == "discard"]


class TestMoveCardsRetiresBoardEffects:
    """Moving a card off the board (center/in_play -> anywhere else) retires
    its ongoing effect exactly like destroy_card: hooks unregister and rules
    it set revert."""

    HOOK_SNIPPET = "def apply(state, ctx):\n    state.add_points('self', 10)\n"

    def _hooked_center_state(self, **overrides) -> GameState:
        defaults = dict(
            room_code="TEST",
            players=[Player(id="p1", name="Alice")],
            house_rules=["hr1"],
            cards={"hr1": {"id": "hr1", "title": "Hooked", "description": ""}},
            hooks=[
                HookSpec(
                    id="hook-hr1",
                    source_card_id="hr1",
                    event=str(GameEvent.ON_TURN_START),
                    scope="center",
                    code=self.HOOK_SNIPPET,
                )
            ],
        )
        defaults.update(overrides)
        return GameState(**defaults)

    def test_moving_center_card_to_exile_unregisters_its_hook(self):
        new = apply_op(
            self._hooked_center_state(),
            MoveCardsOp(card_target="id:hr1", to_zone="exile"),
            make_ctx(),
        )
        assert new.exiled == ["hr1"]
        assert new.hooks == []
        assert any("unregistered hr1" in line for line in new.log)

    def test_moving_in_play_card_to_discard_unregisters_its_hook(self):
        state = self._hooked_center_state(players=[Player(id="p1", name="Alice", in_play=["hr1"])], house_rules=[])
        new = apply_op(
            state,
            MoveCardsOp(from_zone="in_play", from_player="id:p1", selector="all", to_zone="discard"),
            make_ctx(),
        )
        assert new.get_player("p1").in_play == []
        assert new.hooks == []

    def test_moving_center_card_off_board_reverts_its_rule(self):
        state = GameState(room_code="TEST", players=[Player(id="p1", name="Alice")], house_rules=["hr1"])
        state = apply_op(state, SetRuleOp(path="draw", value=3), make_ctx(card_id="hr1"))
        assert state.rules.draw == 3
        new = apply_op(
            state,
            MoveCardsOp(from_zone="center", selector="all", to_zone="exile"),
            make_ctx(),
        )
        assert new.exiled == ["hr1"]
        assert new.rules.draw == 1
        assert new.rule_bindings == []

    def test_moving_between_board_zones_keeps_the_hook(self):
        new = apply_op(
            self._hooked_center_state(),
            MoveCardsOp(card_target="id:hr1", to_zone="in_play", to_player="id:p1"),
            make_ctx(),
        )
        assert new.get_player("p1").in_play == ["hr1"]
        assert len(new.hooks) == 1

    def test_moving_unrelated_cards_leaves_hooks_untouched(self):
        state = self._hooked_center_state(deck=["d1"])
        new = apply_op(
            state,
            MoveCardsOp(from_zone="deck", selector="top", count=1, to_zone="discard"),
            make_ctx(),
        )
        assert len(new.hooks) == 1


class TestShuffleDeckReducer:
    def test_injected_rng_is_deterministic(self):
        first = apply_op(make_state(), ShuffleDeckOp(), make_ctx(), rng=random.Random(5))
        second = apply_op(make_state(), ShuffleDeckOp(), make_ctx(), rng=random.Random(5))
        assert first.deck == second.deck
        assert sorted(first.deck) == ["d1", "d2", "d3"]
        assert "[shuffle_deck] shuffled the deck" in first.log

    def test_include_discard_reshuffles_pile_into_deck(self):
        state = make_state(discard=["x", "y"])
        new = apply_op(state, ShuffleDeckOp(include_discard=True), make_ctx(), rng=random.Random(1))
        assert new.discard == []
        assert sorted(new.deck) == ["d1", "d2", "d3", "x", "y"]
        assert "[shuffle_deck] shuffled the discard pile into the deck" in new.log


class TestCreateCardDestinations:
    def _apply(self, destination):
        state = make_state()
        new = apply_op(
            state,
            CreateCardOp(title="Minted", destination=destination),
            make_ctx("p1", card_id="src"),
            rng=random.Random(0),
        )
        (cid,) = [c for c in new.cards if c.startswith("created-")]
        return new, cid

    def test_discard(self):
        new, cid = self._apply("discard")
        assert new.discard == [cid]
        assert new.deck == ["d1", "d2", "d3"]

    def test_center(self):
        new, cid = self._apply("center")
        assert new.house_rules == [cid]

    def test_deck_bottom(self):
        new, cid = self._apply("deck_bottom")
        assert new.deck == ["d1", "d2", "d3", cid]


class TestCompileAuthoring:
    def test_move_cards_maps_players_and_passes_zones(self):
        program = compile_card(
            {
                "ops": [
                    {
                        "op": "move_cards",
                        "args": {"from_zone": "deck", "selector": "top", "count": 3, "to_zone": "discard"},
                    }
                ]
            }
        )
        (op,) = program.ops
        assert op == MoveCardsOp(from_zone="deck", selector="top", count=3, to_zone="discard")

    def test_move_cards_maps_authoring_player_aliases(self):
        program = compile_card(
            {
                "ops": [
                    {
                        "op": "move_cards",
                        "args": {"from_zone": "discard", "to_zone": "hand", "to_player": "player"},
                    }
                ]
            }
        )
        (op,) = program.ops
        assert op.to_player == "chooser"
        assert program.requires_choice

    def test_move_cards_missing_to_zone_is_skipped(self):
        assert compile_card({"ops": [{"op": "move_cards", "args": {"from_zone": "deck"}}]}) is None

    def test_shuffle_deck(self):
        program = compile_card({"ops": [{"op": "shuffle_deck", "args": {"include_discard": True}}]})
        assert program.ops == [ShuffleDeckOp(include_discard=True)]


class TestSandboxMirrors:
    def _game(self):
        state = {
            "players": [
                {"id": "p1", "name": "A", "score": 0, "hand": ["c1"]},
                {"id": "p2", "name": "B", "score": 0, "hand": ["c2"]},
            ],
            "deck": ["d1", "d2"],
            "turn_index": 0,
        }
        return SandboxGame(state, {"actor_id": "p1"})

    def test_move_cards_records_full_op_and_returns_nothing(self):
        g = self._game()
        assert g.move_cards(from_zone="deck", selector="top", count=2, to_zone="discard") is None
        assert g.ops() == [
            {
                "op": "move_cards",
                "card_target": None,
                "from_zone": "deck",
                "selector": "top",
                "count": 2,
                "from_player": None,
                "to_zone": "discard",
                "to_position": "top",
                "to_player": None,
            }
        ]

    def test_shuffle_deck_records_op(self):
        g = self._game()
        g.shuffle_deck(include_discard=True)
        assert g.ops() == [{"op": "shuffle_deck", "include_discard": True}]

    def test_move_cards_validation_mirrors_the_model(self):
        g = self._game()
        with pytest.raises(ValueError, match="exactly one of card_target or from_zone"):
            g.move_cards(to_zone="discard")
        with pytest.raises(ValueError, match="exactly one of card_target or from_zone"):
            g.move_cards(card_target="this", from_zone="deck", to_zone="discard")
        with pytest.raises(ValueError, match="from_player is required"):
            g.move_cards(from_zone="hand", to_zone="discard")
        with pytest.raises(ValueError, match="to_player is required"):
            g.move_cards(from_zone="deck", to_zone="hand")
        with pytest.raises(ValueError, match="to_player is required"):
            g.move_cards(from_zone="deck", to_zone="discard", to_player="self")
        with pytest.raises(ValueError, match="count"):
            g.move_cards(from_zone="deck", to_zone="discard", count=0)
        with pytest.raises(ValueError, match="count"):
            g.move_cards(from_zone="deck", to_zone="discard", count=51)
        with pytest.raises(ValueError, match="selector"):
            g.move_cards(from_zone="deck", to_zone="discard", selector="middle")
        with pytest.raises(ValueError, match="to_position"):
            g.move_cards(from_zone="deck", to_zone="deck", to_position="middle")
        with pytest.raises(ValueError, match="from_zone"):
            g.move_cards(from_zone="graveyard", to_zone="discard")
        with pytest.raises(ValueError, match="to_zone"):
            g.move_cards(from_zone="deck", to_zone="graveyard")
        assert g.ops() == []

    def test_recorded_move_cards_revalidates_and_applies(self):
        g = self._game()
        g.move_cards(from_zone="deck", selector="all", to_zone="exile")
        new = apply_snippet_diff(make_state(), g.ops(), make_ctx(), rng=random.Random(0))
        assert new.deck == []
        assert new.exiled == ["d1", "d2", "d3"]

    def test_recorded_shuffle_deck_revalidates(self):
        g = self._game()
        g.shuffle_deck()
        program = parse_diff(g.ops())
        assert program.ops == [ShuffleDeckOp()]

    def test_choice_requiring_move_cards_rejected_in_diffs(self):
        raw = [{"op": "move_cards", "from_zone": "hand", "from_player": "chooser", "to_zone": "discard"}]
        with pytest.raises(DiffValidationError, match="choice-requiring"):
            parse_diff(raw)
