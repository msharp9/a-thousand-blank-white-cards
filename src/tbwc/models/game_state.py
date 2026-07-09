"""tbwc.models.game_state — GameState, Player, WinCondition and mutable loop config.

GameState is the single snapshot the whole engine reads and writes. Reducers
take a GameState and return a new GameState.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, PrivateAttr


class WinCondition(BaseModel):
    kind: Literal[
        "highest_points",
        "lowest_points",
        "first_to",
        "last_standing",
        "none",
    ] = "highest_points"
    threshold: int | None = None  # used by "first_to"


class Player(BaseModel):
    id: str
    name: str
    score: int = 0
    hand: list[str] = Field(default_factory=list)  # card ids
    connected: bool = True


class GameState(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    room_code: str
    players: list[Player] = Field(default_factory=list)

    # Card registry grows during play as new cards are invented
    deck: list[str] = Field(default_factory=list)  # card ids (ordered)
    discard: list[str] = Field(default_factory=list)  # card ids
    cards: dict[str, Any] = Field(default_factory=dict)  # card_id -> Card

    turn_index: int = 0  # index into players list

    # Mutable loop configuration — cards can rewrite these
    direction: Literal[1, -1] = 1  # 1 = clockwise, -1 = counter-clockwise
    draw_count: int = 1  # cards drawn at start of each turn
    # skip_predicate: None or a serializable rule-ref string.
    skip_predicate: str | None = None

    win_condition: WinCondition = Field(default_factory=WinCondition)

    # Hook registry: list of RegisteredHook ids (actual objects in HookRegistry)
    persistent_effects: list[str] = Field(default_factory=list)

    # House rules: ids of CENTER-scoped cards currently in effect
    house_rules: list[str] = Field(default_factory=list)

    phase: Literal["lobby", "setup", "playing", "epilogue", "ended"] = "lobby"

    log: list[str] = Field(default_factory=list)

    # Engine-internal turn bookkeeping (not serialized).
    _skip_next: set[str] = PrivateAttr(default_factory=set)
    _extra_turn: set[str] = PrivateAttr(default_factory=set)

    def active_player(self) -> Player:
        """Return the player whose turn it currently is."""
        return self.players[self.turn_index % len(self.players)]

    def get_player(self, player_id: str) -> Player:
        for p in self.players:
            if p.id == player_id:
                return p
        raise KeyError(f"Player {player_id!r} not found")

    def with_log(self, msg: str) -> GameState:
        """Return a copy of this state with msg appended to log."""
        return self.model_copy(update={"log": [*self.log, msg]})

    def copy_with_turn_flags(
        self,
        *,
        turn_index: int | None = None,
        skip_next: set[str] | None = None,
        extra_turn: set[str] | None = None,
    ) -> "GameState":
        """Return a copy that ALWAYS rebinds BOTH private turn-flag sets to fresh
        copies (defaulting to copies of the current values), so the source state's
        private sets are never shared or mutated. Optionally updates turn_index."""
        update = {}
        if turn_index is not None:
            update["turn_index"] = turn_index
        new = self.model_copy(update=update)
        new._skip_next = set(self._skip_next if skip_next is None else skip_next)
        new._extra_turn = set(self._extra_turn if extra_turn is None else extra_turn)
        return new
