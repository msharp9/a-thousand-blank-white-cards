"""engine.scoring — evaluate the win condition and produce winner ids."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from engine.apply import apply_effect
from engine.compile import compile_card
from engine.events import GameEvent, HookContext
from models.game_state import GameState


@dataclass(frozen=True)
class EndOfGameApplication:
    """One ``on_game_end`` card's effect, as actually applied to a GameState.

    ``deltas`` maps player id to the score change caused by this single
    card (only players whose score actually moved are included), computed by
    diffing scores immediately before and after this card's ``apply_effect``.
    """

    holder_id: str
    holder_name: str
    card_id: str
    card_title: str
    deltas: dict[str, int] = field(default_factory=dict)


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


def resolve_end_of_game(state: GameState, cards: dict | None = None) -> tuple[GameState, list[EndOfGameApplication]]:
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

    Returns the updated state alongside one :class:`EndOfGameApplication` per
    card that actually moved a score, in application order, so callers (e.g.
    Room) can surface each application to players instead of applying it
    silently.
    """
    registry = cards if cards is not None else state.cards
    applications: list[EndOfGameApplication] = []

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
            before = {p.id: p.score for p in state.players}
            state = apply_effect(state, program, ctx)
            deltas = {p.id: p.score - before[p.id] for p in state.players if p.score != before[p.id]}
            if deltas:
                applications.append(
                    EndOfGameApplication(
                        holder_id=player.id,
                        holder_name=player.name,
                        card_id=card_id,
                        card_title=card.get("title") or "a card",
                        deltas=deltas,
                    )
                )

    return state, applications


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
        case "empty_hand":
            return [p.id for p in active if not p.hand]
        case "last_standing":
            return [active[0].id] if len(active) == 1 else []
        case "none":
            return []
        case _:
            return []


_LIVE_WIN_KINDS = frozenset({"first_to", "empty_hand", "last_standing"})


def win_condition_met(state: GameState) -> bool:
    """True if the CURRENT win_condition is genuinely satisfied mid-play.

    Used by Room to evaluate set_win_condition-driven endings DURING play.
    Only kinds in ``_LIVE_WIN_KINDS`` can fire here: "highest_points" /
    "lowest_points" always resolve to a winner once any active player exists,
    so they stay end-of-game-only (deck exhaustion / an ``end_game`` op).
    A ``first_to`` without a positive threshold is degenerate — every player
    would qualify instantly ("threshold or 0" ≥ any non-negative score) — so
    it is inert live and only decides winners once the game ends.
    """
    wc = state.win_condition
    if wc.kind not in _LIVE_WIN_KINDS:
        return False
    if wc.kind == "first_to" and (wc.threshold is None or wc.threshold < 1):
        return False
    return bool(evaluate_win_condition(state))


def evaluate_end_condition(state: GameState) -> bool:
    """True when ``rules.end_condition`` is currently met.

    Data-driven end-of-game check (docs/state-example.jsonc ``endCondition``).
    The Room decides TIMING: "deck_empty" defers to the drawer finishing their
    turn; every other type ends play immediately (see board.rooms.room).
    """
    ec = state.rules.end_condition
    match ec.type:
        case "deck_empty":
            return not state.deck
        case "empty_hand":
            return any(not p.hand for p in state.players)
        case "points_reached":
            if ec.threshold is None:
                return False
            return any(p.score >= ec.threshold for p in state.players)
        case "now":
            return True
        case _:
            return False


def check_win(state: GameState) -> GameState:
    """Check win condition; if a winner exists, set phase to 'ended' and log.

    Intended as an ON_WIN_CHECK hook handler. Returns updated GameState.
    """
    winners = evaluate_win_condition(state)
    if winners:
        from engine.history import record_game_end

        msg = f"Game over! Winner(s): {', '.join(winners)}"
        state = state.model_copy(update={"phase": "ended"}).with_log(msg)
        return record_game_end(state, winners, source="check_win")
    return state
