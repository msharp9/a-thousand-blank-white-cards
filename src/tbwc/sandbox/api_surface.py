"""tbwc.sandbox.api_surface — restricted façade passed to LLM snippet apply().

A snippet's apply(state, ctx) receives a SandboxGame; it CANNOT touch raw
GameState. Each mutating call records an op dict in self._ops. After apply()
returns, the parent collects self.ops() as a JSON list (the diff) and re-validates
it through the engine's own reducers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class _PlayerView:
    """Read-only window into a single player's public state."""

    id: str
    name: str
    score: int
    hand_size: int
    connected: bool


class SandboxGame:
    """Restricted game façade injected into snippet execution.

    Exposes read-only player views and whitelisted mutators that record ops as
    dicts. Instantiated inside the sandboxed subprocess from JSON-decoded
    state/ctx; records ops which the child serialises to stdout for the parent.
    """

    def __init__(self, state_dict: dict[str, Any], ctx_dict: dict[str, Any]) -> None:
        self._state = state_dict
        self._ctx = ctx_dict
        self._ops: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Read-only views
    # ------------------------------------------------------------------

    @property
    def current_player_id(self) -> str:
        """Id of the player whose turn it is."""
        players = self._state["players"]
        idx = self._state.get("turn_index", 0) % len(players)
        return players[idx]["id"]

    @property
    def actor_id(self) -> str:
        """Id of the player who triggered the event (from ctx)."""
        return self._ctx.get("actor_id", self.current_player_id)

    def players(self) -> list[_PlayerView]:
        """Read-only views of all players."""
        return [self._view(p) for p in self._state["players"]]

    def player(self, player_id: str) -> _PlayerView:
        """Read-only view of a specific player by id."""
        for p in self._state["players"]:
            if p["id"] == player_id:
                return self._view(p)
        raise KeyError(f"Player {player_id!r} not found")

    @staticmethod
    def _view(p: dict[str, Any]) -> _PlayerView:
        return _PlayerView(
            id=p["id"],
            name=p["name"],
            score=p["score"],
            hand_size=len(p.get("hand", [])),
            connected=p.get("connected", True),
        )

    @property
    def draw_count(self) -> int:
        return self._state.get("draw_count", 1)

    @property
    def direction(self) -> int:
        return self._state.get("direction", 1)

    # ------------------------------------------------------------------
    # Mutators — each appends an op dict; never modifies _state/_ctx
    # ------------------------------------------------------------------

    def add_points(self, target: str, amount: int) -> None:
        """Award `amount` points to player `target`."""
        self._require_nonneg_int(amount)
        self._ops.append({"op": "add_points", "target": target, "amount": amount})

    def subtract_points(self, target: str, amount: int) -> None:
        """Deduct `amount` points from player `target`."""
        self._require_nonneg_int(amount)
        self._ops.append({"op": "subtract_points", "target": target, "amount": amount})

    def set_points(self, target: str, amount: int) -> None:
        """Set player `target`'s score to exactly `amount`."""
        if not isinstance(amount, int) or isinstance(amount, bool):
            raise ValueError(f"amount must be an int, got {amount!r}")
        self._ops.append({"op": "set_points", "target": target, "amount": amount})

    def skip(self, target: str) -> None:
        """Skip player `target`'s next turn."""
        self._ops.append({"op": "skip_turn", "target": target})

    def set_draw_count(self, amount: int) -> None:
        """Set the per-turn draw count."""
        if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
            raise ValueError(f"draw count must be a non-negative int, got {amount!r}")
        self._ops.append({"op": "change_draw_count", "amount": amount})

    def note(self, message: str) -> None:
        """Log a flavour message (no mechanical effect)."""
        self._ops.append({"op": "custom_note", "note": str(message)[:500]})

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _require_nonneg_int(amount: int) -> None:
        if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
            raise ValueError(f"amount must be a non-negative int, got {amount!r}")

    def ops(self) -> list[dict[str, Any]]:
        """Return a copy of recorded ops for serialisation."""
        return list(self._ops)
