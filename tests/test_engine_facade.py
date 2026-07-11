"""Tests for engine.facade.GameEngine.

Each test proves a facade method delegates to the existing engine function and
matches its behaviour — the facade is a naming/ergonomics layer, not a rewrite.
"""

from __future__ import annotations

import sys

import pytest

from engine import GameEngine as ReExportedGameEngine
from engine.apply import apply_effect
from engine.compile import compile_card
from engine.events import GameEvent, HookContext
from engine.facade import GameEngine
from engine.loop import draw_step
from engine.reducers import apply_op
from engine.scoring import check_win, evaluate_win_condition
from models.effects import AddPointsOp, SubtractPointsOp
from models.game_state import GameState, Player, WinCondition


def make_state(players=None, deck=None, draw_count=1, win_condition=None) -> GameState:
    if players is None:
        players = [
            Player(id="p1", name="Alice", score=10, hand=["c1", "c2"]),
            Player(id="p2", name="Bob", score=5, hand=["c3"]),
            Player(id="p3", name="Carol", score=20, hand=[]),
        ]
    return GameState(
        room_code="TEST",
        players=players,
        deck=deck if deck is not None else ["d1", "d2", "d3"],
        draw_count=draw_count,
        turn_index=0,
        win_condition=win_condition or WinCondition(),
    )


def make_ctx(actor_id="p1") -> HookContext:
    return HookContext(event=GameEvent.ON_PLAY, actor_id=actor_id)


@pytest.fixture
def engine() -> GameEngine:
    return GameEngine()


def test_reexport_is_same_class():
    """`from engine import GameEngine` must be the facade class."""
    assert ReExportedGameEngine is GameEngine


class TestAddPoints:
    @pytest.mark.parametrize("amount", [1, 5, 0, -3])
    def test_changes_only_target(self, engine, amount):
        state = make_state()
        result = engine.add_points(state, "p2", amount)
        assert result.get_player("p2").score == 5 + amount
        # others unchanged
        assert result.get_player("p1").score == 10
        assert result.get_player("p3").score == 20

    def test_matches_apply_op(self, engine):
        """Equivalence with constructing the op + apply_op directly."""
        state = make_state()
        via_facade = engine.add_points(state, "p2", 7)
        op = AddPointsOp(target="self", amount=7)
        direct = apply_op(state, op, make_ctx("p2"))
        assert [p.score for p in via_facade.players] == [p.score for p in direct.players]

    def test_does_not_mutate_source(self, engine):
        state = make_state()
        engine.add_points(state, "p1", 100)
        assert state.get_player("p1").score == 10

    def test_supplied_ctx_actor_is_overridden_to_target(self, engine):
        """A passed ctx with a different actor still credits player_id.

        The point ops always target "self", so the facade forces actor_id to the
        requested player_id regardless of the ctx's own actor.
        """
        state = make_state()
        ctx = HookContext(event=GameEvent.ON_PLAY, actor_id="p1", card_id="cX")
        result = engine.add_points(state, "p2", 4, ctx=ctx)
        assert result.get_player("p2").score == 9
        assert result.get_player("p1").score == 10


class TestSubtractPoints:
    @pytest.mark.parametrize("amount", [1, 4, 0])
    def test_changes_only_target(self, engine, amount):
        state = make_state()
        result = engine.subtract_points(state, "p1", amount)
        assert result.get_player("p1").score == 10 - amount
        assert result.get_player("p2").score == 5
        assert result.get_player("p3").score == 20

    def test_matches_apply_op(self, engine):
        state = make_state()
        via_facade = engine.subtract_points(state, "p3", 6)
        op = SubtractPointsOp(target="self", amount=6)
        direct = apply_op(state, op, make_ctx("p3"))
        assert [p.score for p in via_facade.players] == [p.score for p in direct.players]


class TestDraw:
    def test_matches_draw_step(self, engine):
        state = make_state()
        via_facade = engine.draw(state, "p1")
        direct = draw_step(state, "p1")
        assert [p.hand for p in via_facade.players] == [p.hand for p in direct.players]
        assert via_facade.deck == direct.deck

    def test_draws_draw_count_cards(self, engine):
        state = make_state(draw_count=2)
        result = engine.draw(state, "p1")
        # p1 started with c1,c2; draws top 2 of the deck
        assert result.get_player("p1").hand == ["c1", "c2", "d1", "d2"]
        assert result.deck == ["d3"]

    def test_empty_deck_ends_game(self, engine):
        state = make_state(deck=[])
        result = engine.draw(state, "p1")
        assert result.phase == "ended"


class TestResolveCard:
    def test_compilable_card_matches_apply_effect(self, engine):
        """A card with structured ops resolves via apply_effect(compile_card)."""
        card = {"title": "Gain 3", "ops": [{"op": "add_points", "args": {"target": "self", "amount": 3}}]}
        ctx = make_ctx("p2")
        program = compile_card(card)
        assert program is not None and program.ops  # sanity: it compiles
        via_facade = engine.resolve_card(make_state(), card, ctx)
        direct = apply_effect(make_state(), program, ctx)
        assert [p.score for p in via_facade.players] == [p.score for p in direct.players]
        assert via_facade.get_player("p2").score == 8

    def test_free_text_card_is_deterministic_and_calls_no_llm(self, engine, monkeypatch):
        """A non-compilable card must NOT touch any agent/LLM path.

        We ensure the agent package is not even imported by the resolve path: if
        the facade tried to import it we'd see it appear in sys.modules.
        """
        # A free-text card with no structured ops does not compile.
        card = {"title": "Do something whimsical", "description": "the table cheers"}
        assert compile_card(card) is None

        sys.modules.pop("agent", None)
        result = engine.resolve_card(make_state(), card, make_ctx("p1"))

        # No LLM/agent import was triggered.
        assert "agent" not in sys.modules
        # Deterministic: scores unchanged, a note is logged, no exception.
        assert [p.score for p in result.players] == [10, 5, 20]
        assert any("Do something whimsical" in line for line in result.log)

    def test_free_text_result_is_stable(self, engine):
        """Same free-text input yields the same deterministic output twice."""
        card = {"title": "Flavor", "description": "just vibes"}
        state = make_state()
        first = engine.resolve_card(state, card, make_ctx("p1"))
        second = engine.resolve_card(state, card, make_ctx("p1"))
        assert first.log == second.log


class TestCheckEndGame:
    def test_matches_check_win_no_winner(self, engine):
        # first_to threshold not reached -> nobody wins yet; state returned as-is
        state = make_state(win_condition=WinCondition(kind="first_to", threshold=100))
        via_facade = engine.check_end_game(state)
        direct = check_win(state)
        assert via_facade.model_dump() == direct.model_dump()
        assert via_facade.phase == state.phase  # unchanged, no winner

    def test_matches_check_win_with_winner(self, engine):
        state = make_state(win_condition=WinCondition(kind="highest_points"))
        via_facade = engine.check_end_game(state)
        direct = check_win(state)
        assert via_facade.phase == direct.phase == "ended"
        assert via_facade.log == direct.log


class TestDetermineWinner:
    def test_matches_evaluate_win_condition(self, engine):
        state = make_state(win_condition=WinCondition(kind="highest_points"))
        assert engine.determine_winner(state) == evaluate_win_condition(state) == ["p3"]

    def test_no_winner(self, engine):
        state = make_state(win_condition=WinCondition(kind="none"))
        assert engine.determine_winner(state) == evaluate_win_condition(state) == []


class TestUpdateHistory:
    def test_matches_with_log(self, engine):
        state = make_state()
        via_facade = engine.update_history(state, "hello")
        direct = state.with_log("hello")
        assert via_facade.log == direct.log == ["hello"]

    def test_appends(self, engine):
        state = make_state().with_log("first")
        result = engine.update_history(state, "second")
        assert result.log == ["first", "second"]
