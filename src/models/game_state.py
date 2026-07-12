"""models.game_state — GameState, Player, WinCondition and mutable loop config.

GameState is the single snapshot the whole engine reads and writes. Reducers
take a GameState and return a new GameState.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


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
    # Cards played and persisting "in front of" this player (the in-play zone).
    in_play: list[str] = Field(default_factory=list)  # card ids
    connected: bool = True
    # A spectator joined AFTER the game left the lobby. They still live in
    # `players` (so the WS layer, connection manager and snapshot keep working
    # over a single list — no parallel `spectators` collection to thread
    # through every layer), but they take no turn, are never dealt/auto-drawn
    # to, cannot author or play cards, and are excluded from win scoring.
    spectator: bool = False
    # Open-ended per-player status bag, e.g. {"skip_next": True, "poisoned": 2}.
    # "skip_next" and "extra_turn" are reserved keys consumed by
    # engine.loop.advance_turn; any other key is free-form status with no
    # engine-side meaning yet, surfaced as-is to the UI/agent via model_dump().
    conditions: dict[str, Any] = Field(default_factory=dict)


class GameState(BaseModel):
    """The single game snapshot the whole engine reads and writes.

    Card zone taxonomy (a card id lives in exactly one zone at a time):

    - ``deck``            — global draw pile (ordered), on ``GameState``.
    - ``hand``            — per-player private hand, on each ``Player``.
    - ``in_play``         — per-player "in front of me" zone of played,
                            persistent cards, on each ``Player``.
    - ``center``          — shared table-center zone of CENTER-scoped cards
                            currently in effect. This is stored in
                            ``house_rules`` (kept for backward compat); use the
                            ``center_cards()`` accessor to read it by zone name.
    - ``discard``         — global discard pile, on ``GameState``.

    Zone read/move helpers (``cards_in_play``, ``cards_in_play_for``,
    ``center_cards``, ``move_card``) exist for a future CardTarget resolver.
    """

    model_config = {"arbitrary_types_allowed": True}

    room_code: str
    # Room mode chosen at creation: "online", "in_person", or "both". A later
    # bead uses it to filter the deck by card venue; here it just rides in every
    # snapshot via model_dump().
    mode: Literal["online", "in_person", "both"] = "both"
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

    # House rules == the CENTER zone: ids of CENTER-scoped cards currently in
    # effect / placed in the shared table center. Read via center_cards().
    house_rules: list[str] = Field(default_factory=list)

    phase: Literal["lobby", "setup", "playing", "epilogue", "ended"] = "lobby"

    # Serialized signal set by EndGameOp's reducer: a card said "end the game".
    # Room checks this (and evaluate_win_condition) DURING play — see
    # board.rooms.room._handle_play / _advance_turn — rather than only at deck
    # exhaustion. A future rules-as-data bead folds this into a generic
    # rules.end_condition; keep this a plain bool until then.
    game_over_requested: bool = False

    # Winner player ids, populated when the game ends (phase == "ended"). A tie
    # yields multiple ids; an empty list means "no winner" (e.g. win_condition
    # "none"). Surfaced in the snapshot so the frontend can render a win/lose
    # result without parsing the log.
    winner_ids: list[str] = Field(default_factory=list)

    log: list[str] = Field(default_factory=list)

    def turn_players(self) -> list[Player]:
        """Players who participate in the turn rotation (non-spectators).

        Spectators join after the game leaves the lobby; they live in
        ``players`` so they still receive state and appear on the table, but
        they never take a turn. ``turn_index`` always points at a
        non-spectator (guaranteed by game start and ``advance_turn``), so
        ``active_player`` can still index ``players`` directly — this helper
        exists for callers (scoring, tests) that need the participating set.
        """
        return [p for p in self.players if not p.spectator]

    def active_player(self) -> Player:
        """Return the player whose turn it currently is.

        ``turn_index`` is maintained (by game start and ``advance_turn``) to
        always reference a non-spectator, so this indexes ``players`` directly.
        """
        return self.players[self.turn_index % len(self.players)]

    def get_player(self, player_id: str) -> Player:
        for p in self.players:
            if p.id == player_id:
                return p
        raise KeyError(f"Player {player_id!r} not found")

    # ── card-zone read helpers ──
    def cards_in_play(self) -> list[str]:
        """Return the union of every player's in-play cards, in player order."""
        return [card_id for p in self.players for card_id in p.in_play]

    def cards_in_play_for(self, player_id: str) -> list[str]:
        """Return the in-play (in-front-of) cards for a single player."""
        return list(self.get_player(player_id).in_play)

    def center_cards(self) -> list[str]:
        """Return the cards in the shared center zone (stored in house_rules)."""
        return list(self.house_rules)

    def move_card(
        self,
        card_id: str,
        from_zone: Literal["hand", "in_play", "center", "discard", "deck"],
        to_zone: Literal["hand", "in_play", "center", "discard", "deck"],
        *,
        from_player_id: str | None = None,
        to_player_id: str | None = None,
    ) -> GameState:
        """Return a copy of this state with card_id moved between zones.

        Player-scoped zones (``hand``, ``in_play``) require the corresponding
        ``*_player_id``; global zones (``center``, ``discard``, ``deck``) ignore
        them. The card is removed from every occurrence in the source zone and
        appended to the destination zone. This is immutable: the source state,
        its players and its lists are never mutated.
        """
        players = list(self.players)
        update: dict[str, Any] = {}

        def _player_zone(pid: str | None, zone: str) -> None:
            if pid is None:
                raise ValueError(f"Zone {zone!r} requires a player id")

        # ── remove from source ──
        if from_zone in ("hand", "in_play"):
            _player_zone(from_player_id, from_zone)
            players = [
                p.model_copy(update={from_zone: [c for c in getattr(p, from_zone) if c != card_id]})
                if p.id == from_player_id
                else p
                for p in players
            ]
        elif from_zone == "center":
            update["house_rules"] = [c for c in self.house_rules if c != card_id]
        elif from_zone == "discard":
            update["discard"] = [c for c in self.discard if c != card_id]
        elif from_zone == "deck":
            update["deck"] = [c for c in self.deck if c != card_id]

        # ── add to destination ──
        if to_zone in ("hand", "in_play"):
            _player_zone(to_player_id, to_zone)
            players = [
                p.model_copy(update={to_zone: [*getattr(p, to_zone), card_id]}) if p.id == to_player_id else p
                for p in players
            ]
        elif to_zone == "center":
            base = update.get("house_rules", list(self.house_rules))
            update["house_rules"] = [*base, card_id]
        elif to_zone == "discard":
            base = update.get("discard", list(self.discard))
            update["discard"] = [*base, card_id]
        elif to_zone == "deck":
            base = update.get("deck", list(self.deck))
            update["deck"] = [*base, card_id]

        update["players"] = players
        return self.model_copy(update=update)

    def with_log(self, msg: str) -> GameState:
        """Return a copy of this state with msg appended to log."""
        return self.model_copy(update={"log": [*self.log, msg]})

    def with_condition(self, player_id: str, key: str, value: Any) -> "GameState":
        """Return a copy with ``player_id``'s ``conditions[key]`` set to ``value``.

        Generic: ``key`` may be a reserved condition (``skip_next``,
        ``extra_turn``) or any free-form status a card invents.
        """
        players = [
            p.model_copy(update={"conditions": {**p.conditions, key: value}}) if p.id == player_id else p
            for p in self.players
        ]
        return self.model_copy(update={"players": players})

    def without_condition(self, player_id: str, key: str) -> "GameState":
        """Return a copy with ``player_id``'s ``conditions[key]`` removed.

        A no-op (still returns a fresh copy) if the key is absent.
        """
        players = [
            p.model_copy(update={"conditions": {k: v for k, v in p.conditions.items() if k != key}})
            if p.id == player_id
            else p
            for p in self.players
        ]
        return self.model_copy(update={"players": players})
