"""Context-bound access to privacy-safe structured game history."""

from __future__ import annotations

import json
from typing import Any, Literal

from langchain_core.tools import StructuredTool

from engine.history import draw_totals, public_history
from models.game_state import GameState


def make_read_game_history_tool(state: GameState | dict[str, Any]):
    snapshot = state if isinstance(state, GameState) else GameState.model_validate(state)

    def read_game_history(
        aggregate: Literal["events", "draw_totals"] = "events",
        kind: str | None = None,
        player_id: str | None = None,
        limit: int = 100,
    ) -> str:
        """Read public mechanics events or exact draw totals; private hand contents are never returned."""
        if aggregate == "draw_totals":
            return json.dumps({"draw_totals": draw_totals(snapshot)}, sort_keys=True)
        return json.dumps(
            {"events": public_history(snapshot, kind=kind, player_id=player_id, limit=limit)},
            sort_keys=True,
        )

    return StructuredTool.from_function(
        func=read_game_history,
        name="read_game_history",
        description=(
            "Read the append-only public mechanics ledger. Query events by kind/player or request exact "
            "draw_totals. It contains counts and public ids, never private hand contents."
        ),
    )
