"""Phase 1 capstone: end-to-end scripted-game integration test.

Each test is fully SELF-CONTAINED — pytest gives every test method a fresh
fixture, so no state carries across methods. Every test builds its own
GameState via ``build_initial_state()`` and drives its own turn sequence.

These tests exercise the whole engine stack together: the turn loop
(``run_turn``/``draw_step``), the effect pipeline (``apply_effect`` +
reducers), the event bus + hook dispatch (``fire_hooks``), and scoring
(``evaluate_win_condition``).
"""

from __future__ import annotations

from typing import Any

from engine.apply import apply_effect
from engine.events import EventBus, GameEvent, HookContext
from engine.hooks import HookRegistry, RegisteredHook, fire_hooks
from engine.loop import run_turn
from engine.scoring import evaluate_win_condition
from models.cards import Card
from models.effects import (
    AddPointsOp,
    ChangeDrawCountOp,
    CustomNoteOp,
    EffectProgram,
    ReverseOrderOp,
)
from models.game_state import GameState, Player, WinCondition

# ---------------------------------------------------------------------------
# Test infrastructure: bus, helpers, play_fn factory
# ---------------------------------------------------------------------------


class SpyBus(EventBus):
    """An EventBus that records emitted events and dispatches to a registry.

    Wiring the registry in lets tests observe both the event stream AND the
    real hook side-effects (via ``fire_hooks``). Pass an empty registry when a
    test wants event recording but no hook side-effects.
    """

    def __init__(self, registry: HookRegistry) -> None:
        self.registry = registry
        self.emitted: list[str] = []

    def emit(self, event: GameEvent, state: Any, ctx: HookContext) -> Any:
        self.emitted.append(str(event))
        return fire_hooks(state, str(event), ctx, registry=self.registry)


def build_initial_state(
    *,
    players: list[Player] | None = None,
    deck: list[str] | None = None,
    draw_count: int = 1,
    turn_order: list[str] | None = None,
    turn_index: int = 0,
    phase: str = "playing",
    cards: dict[str, Any] | None = None,
    win_condition: WinCondition | None = None,
) -> GameState:
    """Build a playable GameState.

    Defaults: 2 players (Alice=p1, Bob=p2), a 6-card deck, draw_count 1,
    phase "playing".
    """
    if players is None:
        players = [
            Player(id="p1", name="Alice", score=0, hand=[]),
            Player(id="p2", name="Bob", score=0, hand=[]),
        ]
    if deck is None:
        deck = [f"d{i}" for i in range(1, 7)]  # d1..d6
    kwargs: dict[str, Any] = {
        "room_code": "GAME",
        "players": players,
        "deck": list(deck),
        "draw_count": draw_count,
        "turn_order": turn_order or [],
        "turn_index": turn_index,
        "phase": phase,
        "cards": cards or {},
    }
    if win_condition is not None:
        kwargs["win_condition"] = win_condition
    return GameState(**kwargs)


def make_play_fn(program: EffectProgram):
    """Return a play_fn that plays ``program`` for the active player."""

    def play_fn(state: GameState, player_id: str):
        ctx = HookContext(event=GameEvent.ON_PLAY, actor_id=player_id, card_id=None)
        return state, program, ctx

    return play_fn


def empty_play_fn(state: GameState, player_id: str):
    """A play_fn that plays nothing (empty program)."""
    ctx = HookContext(event=GameEvent.ON_PLAY, actor_id=player_id)
    return state, EffectProgram(ops=[]), ctx


# ---------------------------------------------------------------------------
# The capstone test class
# ---------------------------------------------------------------------------


class TestFullScriptedGame:
    # -- Scenario 1: reverse + draw-count change through a real turn ---------
    def test_reverse_and_draw_count_change(self) -> None:
        """Bob plays [ReverseOrderOp, ChangeDrawCountOp(2)] -> turn_order reversed, draw_count 2."""
        registry = HookRegistry()  # no hooks: isolate the reducer effects
        bus = SpyBus(registry)
        # turn_index=1 makes Bob (p2) the active player.
        state = build_initial_state(turn_index=1)
        assert state.active_player().id == "p2"

        program = EffectProgram(ops=[ReverseOrderOp(), ChangeDrawCountOp(amount=2)])
        out = run_turn(state, make_play_fn(program), bus=bus)

        assert out.turn_order == ["p2", "p1"]
        assert out.draw_count == 2
        # Original state must be untouched (purity).
        assert state.turn_order == []
        assert state.draw_count == 1

    # -- Scenario 2: persistent doubling hook doubles a score gain -----------
    def test_persistent_doubling_hook_doubles_score(self) -> None:
        """A center ON_SCORE_CHANGE hook doubles the post-op score: 3 -> 6."""
        source_card = Card(
            id="doubler",
            title="Doubler",
            description="Doubles every score change.",
            creator_id="p2",
            properties={},
        )
        state = build_initial_state(cards={"doubler": source_card})

        registry = HookRegistry()

        def doubling_handler(state, ctx):
            new = state
            for pid in ctx.target_player_ids:
                cur = new.get_player(pid).score
                players = [p.model_copy(update={"score": cur * 2}) if p.id == pid else p for p in new.players]
                new = new.model_copy(update={"players": players})
            return new

        registry.register(
            RegisteredHook(
                source_card_id="doubler",
                event=GameEvent.ON_SCORE_CHANGE,
                scope="center",
                owner_id=None,
            ),
            doubling_handler,
        )
        bus = SpyBus(registry)

        ctx = HookContext(event=GameEvent.ON_PLAY, actor_id="p1")
        out = apply_effect(state, EffectProgram(ops=[AddPointsOp(target="self", amount=3)]), ctx, bus=bus)

        # 3 from the op, doubled to 6 by the hook.
        assert out.get_player("p1").score == 6
        # At least one ON_SCORE_CHANGE event was emitted.
        assert bus.emitted.count(str(GameEvent.ON_SCORE_CHANGE)) >= 1

    # -- Scenario 3: game ends when the deck is exhausted --------------------
    def test_game_ends_when_deck_exhausted(self) -> None:
        """Deck of 1, draw_count 1: turn 1 empties the deck; a later turn ends the game."""
        registry = HookRegistry()  # empty: no win-check hook, so ONLY deck exhaustion ends it
        bus = SpyBus(registry)
        state = build_initial_state(deck=["only"], draw_count=1)
        assert state.phase == "playing"

        reached_ended = False
        for _ in range(10):  # small max-iteration guard
            state = run_turn(state, empty_play_fn, bus=bus)
            if state.phase == "ended":
                reached_ended = True
                break

        assert reached_ended, "game never reached the 'ended' phase"
        assert state.phase == "ended"
        # Sanity: the deck really was exhausted along the way.
        assert state.deck == []

    # -- Scenario 4: uncounterable hook resists an override ------------------
    def test_uncounterable_hook_breaks_chain(self) -> None:
        """Player hook A (uncounterable) fires; player hook B never fires."""
        card_a = Card(id="ca", title="A", description="", creator_id="p1", properties={"uncounterable": True})
        card_b = Card(id="cb", title="B", description="", creator_id="p1", properties={})
        state = build_initial_state(cards={"ca": card_a, "cb": card_b})

        registry = HookRegistry()
        fired: list[str] = []
        registry.register(
            RegisteredHook(source_card_id="ca", event=GameEvent.ON_SCORE_CHANGE, scope="player", owner_id="p1"),
            lambda s, ctx: (fired.append("A"), s)[1],
        )
        registry.register(
            RegisteredHook(source_card_id="cb", event=GameEvent.ON_SCORE_CHANGE, scope="player", owner_id="p1"),
            lambda s, ctx: (fired.append("B"), s)[1],
        )

        ctx = HookContext(event=GameEvent.ON_SCORE_CHANGE, actor_id="p1")
        fire_hooks(state, str(GameEvent.ON_SCORE_CHANGE), ctx, registry=registry)

        assert fired == ["A"]  # A fired, chain broke, B did not fire

    # -- Scenario 5: win condition evaluation --------------------------------
    def test_win_condition_highest_points(self) -> None:
        """Alice 15, Bob 5, highest_points -> winner is p1."""
        players = [
            Player(id="p1", name="Alice", score=15),
            Player(id="p2", name="Bob", score=5),
        ]
        state = build_initial_state(players=players, win_condition=WinCondition(kind="highest_points"))
        assert evaluate_win_condition(state) == ["p1"]

    # -- Scenario 6: capstone — multi-turn scripted drive across a reversal --
    def test_multi_turn_scripted_drive(self) -> None:
        """Drive 2 full turns through run_turn; verify events + turn_index across a reversal."""
        # 3 players so a turn_order reversal genuinely changes who plays next.
        players = [
            Player(id="p1", name="Alice", score=0, hand=[]),
            Player(id="p2", name="Bob", score=0, hand=[]),
            Player(id="p3", name="Carol", score=0, hand=[]),
        ]
        registry = HookRegistry()  # empty: events recorded, no side-effects ending the game
        bus = SpyBus(registry)
        state = build_initial_state(players=players, deck=[f"d{i}" for i in range(1, 9)], turn_index=0)

        assert state.active_player().id == "p1"

        # Turn 1: Alice reverses the play order.
        reverse_program = EffectProgram(ops=[ReverseOrderOp()])
        state = run_turn(state, make_play_fn(reverse_program), bus=bus)
        assert state.turn_order == ["p3", "p2", "p1"]
        # order is now [p3, p2, p1]; next after p1 is p3.
        assert state.turn_index == 2
        assert state.active_player().id == "p3"

        # Turn 2: Carol plays a harmless note.
        note_program = EffectProgram(ops=[CustomNoteOp(note="just visiting")])
        state = run_turn(state, make_play_fn(note_program), bus=bus)
        # next after p3 in [p3, p2, p1] is p2.
        assert state.turn_index == 1
        assert state.active_player().id == "p2"

        # All three per-turn lifecycle events were emitted (each turn fires them once).
        assert bus.emitted.count(str(GameEvent.ON_TURN_START)) == 2
        assert bus.emitted.count(str(GameEvent.ON_TURN_END)) == 2
        assert bus.emitted.count(str(GameEvent.ON_WIN_CHECK)) == 2
        # The game is still going (deck was large enough).
        assert state.phase == "playing"


# ---------------------------------------------------------------------------
# Standalone self-contained tests (helpers + extra coverage)
# ---------------------------------------------------------------------------


def test_build_initial_state_defaults() -> None:
    state = build_initial_state()
    assert [p.id for p in state.players] == ["p1", "p2"]
    assert state.get_player("p1").name == "Alice"
    assert state.get_player("p2").name == "Bob"
    assert len(state.deck) == 6
    assert state.phase == "playing"
    assert state.turn_order == []
    assert state.draw_count == 1


def test_win_condition_lowest_points_standalone() -> None:
    players = [Player(id="p1", name="Alice", score=15), Player(id="p2", name="Bob", score=5)]
    state = build_initial_state(players=players, win_condition=WinCondition(kind="lowest_points"))
    assert evaluate_win_condition(state) == ["p2"]


def test_non_uncounterable_chain_runs_to_completion_standalone() -> None:
    """Control for scenario 4: without uncounterable, both player hooks fire in order."""
    card_a = Card(id="ca", title="A", description="", creator_id="p1", properties={})
    card_b = Card(id="cb", title="B", description="", creator_id="p1", properties={})
    state = build_initial_state(cards={"ca": card_a, "cb": card_b})

    registry = HookRegistry()
    fired: list[str] = []
    registry.register(
        RegisteredHook(source_card_id="ca", event=GameEvent.ON_SCORE_CHANGE, scope="player", owner_id="p1"),
        lambda s, ctx: (fired.append("A"), s)[1],
    )
    registry.register(
        RegisteredHook(source_card_id="cb", event=GameEvent.ON_SCORE_CHANGE, scope="player", owner_id="p1"),
        lambda s, ctx: (fired.append("B"), s)[1],
    )

    ctx = HookContext(event=GameEvent.ON_SCORE_CHANGE, actor_id="p1")
    fire_hooks(state, str(GameEvent.ON_SCORE_CHANGE), ctx, registry=registry)
    assert fired == ["A", "B"]
