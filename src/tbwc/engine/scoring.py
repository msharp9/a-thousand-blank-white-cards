"""tbwc.engine.scoring — evaluate the win condition and produce winner ids."""

from __future__ import annotations

from tbwc.models.game_state import GameState


def evaluate_win_condition(state: GameState) -> list[str]:
    """Return list of winner player ids given the current win_condition.

    Returns [] if no winner yet. Multiple ids = a tie. Only considers
    connected players.
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


def check_win(state: GameState) -> GameState:
    """Check win condition; if a winner exists, set phase to 'ended' and log.

    Intended as an ON_WIN_CHECK hook handler. Returns updated GameState.
    """
    winners = evaluate_win_condition(state)
    if winners:
        msg = f"Game over! Winner(s): {', '.join(winners)}"
        return state.model_copy(update={"phase": "ended"}).with_log(msg)
    return state
