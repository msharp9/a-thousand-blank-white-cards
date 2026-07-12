"""agent.tools.read_game_state — a CONTEXT-DEPENDENT board-reading tool.

The interpretation agent needs to react to what is actually happening on the
board: who is winning, whose turn it is, and — crucially — WHO authored the card
it is being asked to interpret (so the ``punish_author`` vs ``do_nothing``
persona branch is decidable). Unlike the context-free tools in this package, this
one cannot be a module-level singleton: it must close over the specific game
snapshot for the current interpretation.

:func:`make_read_game_state_tool` is therefore a FACTORY. It closes over a passed
-in snapshot (a :class:`~models.game_state.GameState` OR a plain ``model_dump()``
dict — both are handled), the ``actor_id`` of the player who played the card, and
an optional ``creator_id`` (the card's author, passed explicitly because the card
being interpreted may not yet be registered in ``state.cards``). It returns a
LangChain ``@tool`` named ``read_game_state``.

Layering: this module imports only ``models`` / stdlib / LangChain — NEVER
``board``. Game state arrives as a passed-in snapshot, exactly per the layering
guard (tests/test_layering.py).
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import StructuredTool

logger = logging.getLogger(__name__)

_UNAVAILABLE = "game state unavailable"


def _getter(obj: Any):
    """Return a ``get(key, default)`` accessor over a dict OR a pydantic/obj.

    Supports both a :class:`~models.game_state.GameState` (attribute access) and a
    plain dict snapshot (key access), so the tool works on either input.
    """
    if isinstance(obj, dict):
        return lambda key, default=None: obj.get(key, default)
    return lambda key, default=None: getattr(obj, key, default)


def _card_creator_id(state: Any, card_id: str | None) -> str | None:
    """Return the ``creator_id`` of ``card_id`` from ``state.cards`` if present.

    Cards in ``state.cards`` may be dicts (the common snapshot form) or objects.
    Returns ``None`` when the card is absent or has no creator_id.
    """
    if not card_id:
        return None
    get = _getter(state)
    cards = get("cards") or {}
    if not isinstance(cards, dict):
        return None
    card = cards.get(card_id)
    if card is None:
        return None
    if isinstance(card, dict):
        return card.get("creator_id")
    return getattr(card, "creator_id", None)


def _resolve_author(state: Any, creator_id: str | None, card_id: str | None) -> str | None:
    """Determine the card author id — the helper the tool uses to compute authorship.

    ``creator_id`` is passed explicitly by ``run_agent`` because the card being
    interpreted may not yet be registered in ``state.cards``; it takes precedence.
    Otherwise we look the card up in ``state.cards`` by ``card_id``. Returns None
    when authorship cannot be determined.
    """
    if creator_id is not None:
        return creator_id
    return _card_creator_id(state, card_id)


def _summarize_state(
    state: Any,
    actor_id: str | None,
    creator_id: str | None,
    card_id: str | None = None,
    focus: str | None = None,
) -> str:
    """Render a concise text summary of the board. Never raises.

    Surfaces, for each player: name, score, hand size, active-player marker,
    spectator marker, and an ACTOR marker (via ``actor_id``). Also surfaces turn
    order, deck size, phase, win condition, and center/house-rule cards, plus
    an actor-vs-author line so the ``punish_author`` persona branch is decidable.
    """
    if state is None:
        return "Game state: (not provided)."

    get = _getter(state)
    lines: list[str] = []

    # ── actor / authorship framing (surfaced first: it drives persona choice) ──
    players = get("players") or []

    def _p(p: Any, key: str, default: Any = None) -> Any:
        return p.get(key, default) if isinstance(p, dict) else getattr(p, key, default)

    actor_name = None
    for p in players:
        if actor_id and _p(p, "id") == actor_id:
            actor_name = _p(p, "name") or actor_id
            break
    if actor_id:
        lines.append(f"You are interpreting a card played by {actor_name or actor_id} (actor_id={actor_id}).")

    author_id = _resolve_author(state, creator_id, card_id)
    if author_id is not None and actor_id is not None:
        if author_id == actor_id:
            lines.append(
                "The ACTOR authored this card themselves (actor == author): "
                "'punish_author' is on the table for a dumb/undecipherable card."
            )
        else:
            lines.append(
                f"The card's author (creator_id={author_id}) is NOT the actor: do NOT "
                "punish the actor for someone else's card ('do_nothing' if undecipherable)."
            )
    elif actor_id is not None:
        lines.append("The card's author is unknown; authorship cannot be confirmed.")

    # ── phase / turn / deck ──
    phase = get("phase")
    if phase:
        lines.append(f"Phase: {phase}.")
    turn_order = get("turn_order")
    if turn_order:
        lines.append(f"Turn order: {' -> '.join(turn_order)}.")
    deck = get("deck") or []
    try:
        lines.append(f"Deck size: {len(deck)} cards remaining.")
    except TypeError:
        pass

    # ── players ──
    turn_index = get("turn_index")
    scored: list[str] = []
    for idx, p in enumerate(players):
        pid = _p(p, "id")
        name = _p(p, "name") or pid
        score = _p(p, "score")
        hand = _p(p, "hand") or []
        try:
            hand_size = len(hand)
        except TypeError:
            hand_size = 0
        tags: list[str] = []
        if isinstance(turn_index, int) and idx == turn_index:
            tags.append("active player")
        if actor_id and pid == actor_id:
            tags.append("ACTOR (played this card)")
        if _p(p, "spectator"):
            tags.append("spectator")
        suffix = f" [{', '.join(tags)}]" if tags else ""
        scored.append(f"  - {name}: {score} points, {hand_size} cards in hand{suffix}")
    if scored:
        lines.append("Players:")
        lines.extend(scored)

    # ── win condition ──
    win = get("win_condition")
    if win is not None:
        wget = _getter(win)
        kind = wget("kind")
        threshold = wget("threshold")
        if kind:
            wc = f"Win condition: {kind}"
            if threshold is not None:
                wc += f" (threshold {threshold})"
            lines.append(wc + ".")

    # ── center / house-rule cards ──
    house = get("house_rules") or []
    try:
        if len(house) > 0:
            lines.append(f"Center / house-rule cards in effect: {len(house)}.")
    except TypeError:
        pass

    if focus and focus.strip():
        lines.append(f"(You asked to focus on: {focus.strip()}.)")

    if not lines:
        return "Game state: (empty)."
    return "\n".join(["Current game state:", *lines])


def make_read_game_state_tool(
    state: Any,
    actor_id: str | None = None,
    creator_id: str | None = None,
    card_id: str | None = None,
):
    """Build a ``read_game_state`` tool closed over this game snapshot.

    Args:
        state: The live snapshot to read — a :class:`~models.game_state.GameState`
            OR a plain ``model_dump()`` dict. Both are handled; never mutated.
        actor_id: The id of the player who played the card being interpreted.
        creator_id: The card's author id, passed explicitly because the card may
            not yet be registered in ``state.cards``. Enables the actor-vs-author
            comparison the ``punish_author`` persona needs.
        card_id: Optional id of the card being interpreted; when ``creator_id`` is
            not supplied, authorship is looked up from ``state.cards[card_id]``.

    Returns:
        A LangChain ``StructuredTool`` named ``read_game_state`` taking an optional
        ``focus`` string and returning a concise text summary. It NEVER raises: any
        error yields the short string ``"game state unavailable"``.
    """

    def read_game_state(focus: str | None = None) -> str:
        """Read the current game state — players, scores, whose turn it is, who authored the card you're interpreting — so you can react to what's happening on the board."""
        try:
            return _summarize_state(state, actor_id, creator_id, card_id, focus)
        except Exception:  # noqa: BLE001 — a bad snapshot must never break the agent
            logger.warning("read_game_state: failed to summarize snapshot", exc_info=True)
            return _UNAVAILABLE

    return StructuredTool.from_function(
        func=read_game_state,
        name="read_game_state",
        description=(
            "Read the current game state — players, scores, whose turn it is, who authored "
            "the card you're interpreting — so you can react to what's happening on the board."
        ),
    )
