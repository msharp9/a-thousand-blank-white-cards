"""evals.game_fixtures — a production-parity GameState for the eval runner.

Real play threads a live ``GameState`` + actor + author into ``run_agent`` and
binds the context tools (read_game_state, read_game_history, dry_run_effect).
The datasets carry no per-card state, so the runner uses ONE deterministic
state for every card: three players mid-game, a stocked deck, ``phase="playing"``.

Two deliberate choices make this exercise the full production path:

* The played card is injected into the actor's hand and card registry under a
  fixed ``EVAL_CARD_ID`` so ``dry_run_resolution_plan`` can model its removal to
  discard (mirroring ``Room._resolve_plan``).
* The actor (``EVAL_ACTOR_ID``) is NOT the author (``EVAL_CREATOR_ID``), so the
  persona branch that distinguishes "played my own card" from "played someone
  else's" (do_nothing vs punish_author) is actually reachable.
* A deterministic choice context (``EVAL_CHOSEN_PLAYER_ID`` / ``EVAL_CHOSEN_CARD_ID``)
  mirrors the production ``prompt_choice`` flow so plans that resolve ``chooser`` /
  ``chosen_card`` targets are executable — without it the reducers raise and every
  choice-based card looks broken.
"""

from __future__ import annotations

from typing import Any

from models.game_state import GameState, Player

EVAL_ACTOR_ID = "p1"
EVAL_CREATOR_ID = "p2"  # author ≠ actor, so persona branching is exercised
EVAL_CARD_ID = "eval-played-card"
EVAL_CHOSEN_PLAYER_ID = "p2"  # a player other than the actor, for chooser/target_player
EVAL_CHOSEN_CARD_ID = "hand-c"  # a real card in another player's hand, for chosen_card


def build_eval_state(title: str = "Eval Card", description: str = "", alt_text: str | None = None) -> GameState:
    """A fresh mid-game state with the card-under-test in the actor's hand.

    A new object every call (never mutate a shared singleton — reducers return
    new states but tools read the passed snapshot). ``alt_text`` rides the card
    registry so the sandbox / read_game_state path can surface the card's art
    description exactly as production does.
    """
    played: dict[str, Any] = {
        "id": EVAL_CARD_ID,
        "title": title,
        "description": description,
        "alt_text": alt_text,
        "attributes": {},
        "creator_id": EVAL_CREATOR_ID,
    }
    return GameState(
        room_code="EVAL",
        players=[
            Player(id="p1", name="Alice", score=7, hand=[EVAL_CARD_ID, "hand-a", "hand-b"]),
            Player(id="p2", name="Bob", score=-4, hand=["hand-c"]),
            Player(id="p3", name="Ivy", score=12, hand=[]),
        ],
        cards={
            EVAL_CARD_ID: played,
            "hand-a": {"id": "hand-a", "title": "Spare", "description": "", "alt_text": None, "attributes": {}},
            "hand-b": {"id": "hand-b", "title": "Spare", "description": "", "alt_text": None, "attributes": {}},
            "hand-c": {"id": "hand-c", "title": "Spare", "description": "", "alt_text": None, "attributes": {}},
        },
        deck=[f"deck-{index}" for index in range(10)],
        phase="playing",
    )
