"""engine.scoring — evaluate the win condition and produce winner ids."""

from __future__ import annotations

from typing import Any

from engine.apply import apply_effect
from engine.compile import compile_card
from engine.events import GameEvent, HookContext
from models.game_state import GameState


def _as_card_dict(card: Any) -> dict | None:
    """Coerce a registry card entry into a plain dict, or None if it can't be."""
    if isinstance(card, dict):
        return card
    dump = getattr(card, "model_dump", None)
    if callable(dump):
        return dump()
    return None


def _game_end_trigger(card: dict) -> bool:
    """True if this card carries an ``on_game_end`` trigger.

    The trigger may live at the top level (``card["trigger"]``) or nested inside
    the canonical annotation (``card["canonical"]["trigger"]``).
    """
    if card.get("trigger") == GameEvent.ON_GAME_END:
        return True
    canonical = card.get("canonical")
    if isinstance(canonical, dict) and canonical.get("trigger") == GameEvent.ON_GAME_END:
        return True
    return False


def resolve_end_of_game(state: GameState, cards: dict | None = None) -> GameState:
    """Apply every ``on_game_end`` card's ops before the winner is decided.

    For each player, every card id in that player's ``hand`` or
    ``in_play`` zone is looked up in the registry (``cards`` if given, else
    ``state.cards``). A card whose canonical (or top-level) ``trigger`` is
    ``on_game_end`` — e.g. "Worth 10 Points If You Keep It" — has its ops
    compiled and applied with the holder as the actor, so ``target="self"`` ops
    credit the right player.

    Pure and immutable: the input ``state`` is never mutated. This computes the
    score adjustments only; setting ``winner_ids`` is left to the caller (which
    runs ``evaluate_win_condition`` next). Cards with no canonical/trigger, and
    cards that compile to ``None``, are skipped.
    """
    registry = cards if cards is not None else state.cards

    for player in state.turn_players():
        for card_id in (*player.hand, *player.in_play):
            card = _as_card_dict(registry.get(card_id))
            if card is None or not _game_end_trigger(card):
                continue
            program = compile_card(card)
            if program is None:
                continue
            ctx = HookContext(
                event=GameEvent.ON_GAME_END,
                actor_id=player.id,
                card_id=card_id,
            )
            state = apply_effect(state, program, ctx)

    return state


def evaluate_win_condition(state: GameState) -> list[str]:
    """Return list of winner player ids given the current win_condition.

    Returns [] if no winner yet. Multiple ids = a tie. Only considers
    connected players — spectators live in the separate ``spectators``
    collection, so ``state.players`` is already the participating set.
    """
    wc = state.win_condition
    active = [p for p in state.players if p.connected]
    if not active:
        return []

    match wc.kind:
        case "highest_points":
            best = max(p.score for p in active)
            return [p.id for p in active if p.score == best]
        case "lowest_points":
            worst = min(p.score for p in active)
            return [p.id for p in active if p.score == worst]
        case "first_to":
            threshold = wc.threshold or 0
            return [p.id for p in active if p.score >= threshold]
        case "last_standing":
            return [active[0].id] if len(active) == 1 else []
        case "none":
            return []
        case _:
            return []


# WinCondition kinds that describe a genuinely mid-game "met" event rather than
# an always-decidable end-of-game ranking. "highest_points"/"lowest_points"
# always have a winner as soon as any active player exists, so treating those
# as "met" would end the game on turn one; "none" never triggers.
_LIVE_WIN_KINDS = frozenset({"first_to", "last_standing"})


def win_condition_met(state: GameState) -> bool:
    """True if the CURRENT win_condition is genuinely satisfied mid-play.

    Used by Room to evaluate set_win_condition-driven endings (e.g. first_to a
    threshold) DURING play, without also firing on "highest_points" /
    "lowest_points" — those kinds always resolve to a winner once any active
    player exists, so they are end-of-game-only and must stay gated behind
    deck exhaustion / an explicit ``end_game`` op.
    """
    if state.win_condition.kind not in _LIVE_WIN_KINDS:
        return False
    return bool(evaluate_win_condition(state))


def check_win(state: GameState) -> GameState:
    """Check win condition; if a winner exists, set phase to 'ended' and log.

    Intended as an ON_WIN_CHECK hook handler. Returns updated GameState.
    """
    winners = evaluate_win_condition(state)
    if winners:
        msg = f"Game over! Winner(s): {', '.join(winners)}"
        return state.model_copy(update={"phase": "ended"}).with_log(msg)
    return state
