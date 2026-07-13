"""engine.facade — GameEngine, an ergonomic wrapper over the pure reducers.

``GameEngine`` exposes the game's "physics" method surface from the owner's
design diagram as thin wrappers over the EXISTING engine functions. It is a
naming / ergonomics layer only: every method delegates to the underlying pure
reducer, loop, scoring, or compile function and reimplements NONE of their
logic.

Layering: this module is pure-engine. It imports ONLY from ``engine.*``,
``models.*`` and shared infra — NEVER from ``agent`` or ``board`` (enforced by
tests/test_layering.py). In particular ``resolve_card`` runs the DETERMINISTIC
compiled-ops path ONLY; LLM interpretation of free-text cards is intentionally
NOT part of this facade. That orchestration (compile → LLM → CustomNote
fallback) lives one layer up in ``board.rooms.room.Room._resolve_program``.
"""

from __future__ import annotations

from engine.apply import apply_effect
from engine.compile import compile_card
from engine.events import EventBus, GameEvent, HookContext
from engine.history import append_history_event
from engine.loop import draw_step
from engine.reducers import apply_op
from engine.scoring import check_win, evaluate_win_condition
from models.effects import AddPointsOp, CustomNoteOp, EffectProgram, SubtractPointsOp
from models.game_state import GameState


class GameEngine:
    """Ergonomic facade over the pure engine reducers.

    Stateless: every method takes a ``GameState`` and returns a NEW one (or, for
    ``determine_winner``, a list of winner ids). No instance state is kept, so a
    single ``GameEngine()`` can be shared freely.
    """

    def add_points(
        self,
        state: GameState,
        player_id: str,
        amount: int,
        *,
        ctx: HookContext | None = None,
    ) -> GameState:
        """Add ``amount`` points to ``player_id`` via the ``add_points`` reducer.

        Builds an ``AddPointsOp(target="self")`` and dispatches it through
        ``apply_op`` with the actor set to ``player_id`` so ``"self"`` resolves to
        exactly that player. Score math lives entirely in the reducer.
        """
        op = AddPointsOp(target="self", amount=amount)
        return apply_op(state, op, self._self_ctx(player_id, ctx))

    def subtract_points(
        self,
        state: GameState,
        player_id: str,
        amount: int,
        *,
        ctx: HookContext | None = None,
    ) -> GameState:
        """Subtract ``amount`` points from ``player_id`` via ``subtract_points``.

        Mirror of :meth:`add_points` using ``SubtractPointsOp``.
        """
        op = SubtractPointsOp(target="self", amount=amount)
        return apply_op(state, op, self._self_ctx(player_id, ctx))

    def draw(self, state: GameState, player_id: str, *, bus: EventBus | None = None) -> GameState:
        """Draw ``player_id``'s turn cards — delegates to ``loop.draw_step``."""
        return draw_step(state, player_id, bus=bus)

    def resolve_card(self, state: GameState, card: dict, ctx: HookContext) -> GameState:
        """Resolve a played card's DETERMINISTIC effect only.

        Compiles the card's structured ops via ``compile_card``; if that yields a
        program with ops, applies it via ``apply_effect``. Otherwise (a free-text
        / non-compilable card) it does NOT invoke any LLM — this facade is
        pure-engine — and instead returns state with a single ``CustomNoteOp`` log
        line, matching ``board.rooms.room.Room._resolve_program``'s deterministic
        fallback so a resolve never silently no-ops.

        NOTE: LLM interpretation of free-text cards is intentionally NOT handled
        here. That orchestration lives in ``board`` (the Room), which is a layer
        above the engine and may reach the agent.
        """
        program = compile_card(card)
        if program is not None and program.ops:
            state = apply_effect(state, program, ctx)
        else:
            title = card.get("title") or "Card"
            fallback = EffectProgram(ops=[CustomNoteOp(note=f"Played {title} (no mechanical effect)")])
            state = apply_effect(state, fallback, ctx)
        return append_history_event(
            state,
            "play",
            actor_id=ctx.actor_id,
            target_player_ids=[ctx.actor_id],
            card_id=ctx.card_id or card.get("id"),
            source="facade",
        )

    def check_end_game(self, state: GameState) -> GameState:
        """Check the win condition and end the game if met — delegates to
        ``scoring.check_win``."""
        return check_win(state)

    def determine_winner(self, state: GameState) -> list[str]:
        """Return the current winner ids — delegates to
        ``scoring.evaluate_win_condition``."""
        return evaluate_win_condition(state)

    def update_history(self, state: GameState, message: str) -> GameState:
        """Append ``message`` to the game log — delegates to ``state.with_log``."""
        return state.with_log(message)

    # ── helpers ──
    @staticmethod
    def _self_ctx(player_id: str, ctx: HookContext | None) -> HookContext:
        """Build the ctx used for a ``target="self"`` point op.

        The point ops above always target ``"self"``, which the reducer resolves
        through ``ctx.actor_id``. To guarantee the op hits ``player_id`` we force
        ``actor_id=player_id`` regardless of any passed ctx (a caller-supplied ctx
        may carry a different actor). When no ctx is supplied we synthesise a
        minimal ON_PLAY context.
        """
        if ctx is None:
            return HookContext(event=GameEvent.ON_PLAY, actor_id=player_id)
        return HookContext(
            event=ctx.event,
            actor_id=player_id,
            card_id=ctx.card_id,
            chosen_player_id=ctx.chosen_player_id,
            chosen_card_id=ctx.chosen_card_id,
            amount=ctx.amount,
            target_player_ids=list(ctx.target_player_ids),
            extra=dict(ctx.extra),
        )


__all__ = ["GameEngine"]
