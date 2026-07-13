"""models.game_state — GameState, Player, WinCondition and mutable loop config.

GameState is the single snapshot the whole engine reads and writes. Reducers
take a GameState and return a new GameState.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field, model_validator

HistoryKind = Literal["draw", "play", "score_change", "rule_change", "interaction", "game_end"]


class WinCondition(BaseModel):
    kind: Literal[
        "highest_points",
        "lowest_points",
        "first_to",
        "empty_hand",
        "last_standing",
        "none",
    ] = "highest_points"
    threshold: int | None = None  # used by "first_to"


class EndCondition(BaseModel):
    """When the game ends, as data (docs/state-example.jsonc ``endCondition``).

    - ``deck_empty``     — default: deck exhausted, drawer finishes their turn.
    - ``empty_hand``     — a player's hand reaches zero cards (Uno-style).
    - ``points_reached`` — any player's score reaches ``threshold``.
    - ``now``            — end immediately (what EndGameOp's reducer sets).
    """

    type: Literal["deck_empty", "empty_hand", "points_reached", "now"] = "deck_empty"
    threshold: int | None = None  # used by "points_reached"


class Rules(BaseModel):
    """Mutable, serialized game rules (docs/state-example.jsonc ``rules``).

    Cards rewrite these via ``set_rule`` / ``change_draw_count`` /
    ``set_win_condition`` reducers. ``extra`` is an open bag for card-invented
    rules the engine has no special handling for yet — hooks/the agent/the UI
    can still read them from the snapshot.
    """

    draw: int = Field(default=1, ge=0)  # cards drawn at start of each turn
    play: int = Field(default=1, ge=0)  # plays allowed per turn
    # What a player who cannot play must do instead (data only; the room's
    # draw-before-pass flow is the current enforcement).
    cannot_play: dict[str, Any] = Field(default_factory=lambda: {"draw": 1})
    end_condition: EndCondition = Field(default_factory=EndCondition)
    win_condition: WinCondition = Field(default_factory=WinCondition)
    # None or a registered predicate name (see engine.loop.register_skip_predicate).
    skip_predicate: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class Player(BaseModel):
    id: str
    name: str
    score: int = 0
    hand: list[str] = Field(default_factory=list)  # card ids
    # Cards played and persisting "in front of" this player (the in-play zone).
    in_play: list[str] = Field(default_factory=list)  # card ids
    connected: bool = True
    # Open-ended per-player status bag, e.g. {"skip_next": True, "poisoned": 2}.
    # "skip_next" and "extra_turn" are reserved keys consumed by
    # engine.loop.advance_turn; any other key is free-form status with no
    # engine-side meaning yet, surfaced as-is to the UI/agent via model_dump().
    conditions: dict[str, Any] = Field(default_factory=dict)


class EpilogueCardOutcome(BaseModel):
    """One voted-on card's outcome, as surfaced to the final results screen."""

    id: str
    title: str


class EpilogueResultSummary(BaseModel):
    """Kept/destroyed epilogue vote outcomes.

    Carries id+title only (not full card bodies) so it rides every snapshot
    (``GameState.epilogue_result``) and survives a reconnect, independent of
    the transient ``EpilogueManager``.
    """

    kept: list[EpilogueCardOutcome] = Field(default_factory=list)
    destroyed: list[EpilogueCardOutcome] = Field(default_factory=list)


class HookSpec(BaseModel):
    """A persistent, serialized hook: sandboxed code that fires on an event.

    Hooks are STATE (docs/state-example.jsonc's "everything is dynamic"):
    they ride ``model_dump`` into snapshots and the room store, so house
    rules survive reconnects/restarts and never leak across rooms. The
    per-room registry/EventBus is a cache DERIVED from this list (see
    engine.hooks.build_registry) — never the source of truth.
    """

    id: str
    source_card_id: str
    event: str  # a GameEvent value, e.g. "on_turn_start"
    scope: Literal["player", "center"] = "center"
    owner_id: str | None = None  # player id for player-scoped hooks
    code: str  # sandbox-validated snippet: def apply(state, ctx)


class HistoryEvent(BaseModel):
    """One privacy-safe, append-only fact about completed game mechanics."""

    sequence: int = Field(ge=1)
    kind: HistoryKind
    actor_id: str | None = None
    target_player_ids: list[str] = Field(default_factory=list)
    card_id: str | None = None
    amount: int | None = None
    source: str | None = None
    rule_path: str | None = None


class Spectator(BaseModel):
    """A watcher who joined AFTER the game left the lobby.

    Lives in ``GameState.spectators`` — a separate, flat collection from
    ``players`` (per docs/state-example.jsonc) — rather than as a flagged
    ``Player``. A spectator is simple and deterministic (just an identity):
    it never takes a turn, is never dealt/auto-drawn to, cannot author or
    play cards, and is excluded from win scoring, structurally rather than by
    a per-call guard.
    """

    id: str
    name: str


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
    # Watchers who joined after the game left the lobby (see Spectator). Kept
    # separate from ``players`` rather than merged in as a flagged Player.
    spectators: list[Spectator] = Field(default_factory=list)

    # Card registry grows during play as new cards are invented
    deck: list[str] = Field(default_factory=list)  # card ids (ordered)
    discard: list[str] = Field(default_factory=list)  # card ids
    cards: dict[str, Any] = Field(default_factory=dict)  # card_id -> Card

    turn_index: int = 0  # index into players list

    # Explicit, ordered, MUTABLE list of player ids describing turn rotation
    # order (the authoritative design in docs/state-example.jsonc: `turnOrder`).
    # Empty means "not yet established" — callers read it via
    # ``effective_turn_order()``, which falls back to ``turn_players()`` order.
    turn_order: list[str] = Field(default_factory=list)

    # Mutable game rules — cards rewrite these through reducers. The canonical
    # home for draw/play counts, end/win conditions and card-invented extras
    # (docs/state-example.jsonc). ``draw_count``/``win_condition``/
    # ``skip_predicate`` remain readable (and serialized) as computed fields
    # below for snapshot/back-compat; writes go through ``rules``.
    rules: Rules = Field(default_factory=Rules)

    # House rules == the CENTER zone: ids of CENTER-scoped cards currently in
    # effect / placed in the shared table center. Read via center_cards().
    house_rules: list[str] = Field(default_factory=list)

    phase: Literal["lobby", "setup", "playing", "results", "epilogue", "ended"] = "lobby"

    # Persistent hooks registered by card plays, in registration order.
    hooks: list[HookSpec] = Field(default_factory=list)

    # Machine-readable history for game logic and reconnects. Unlike ``log``,
    # events never contain private hand contents or generated prose.
    history_events: list[HistoryEvent] = Field(default_factory=list)

    # Winner ids forced by an EndGameOp with a resolved ``winner`` target
    # ("You win the game" cards). When non-empty, _end_game uses these instead
    # of evaluate_win_condition. Consumed (cleared) when the game ends.
    winner_override: list[str] = Field(default_factory=list)

    # Winner player ids, populated when the game ends (phase == "ended"). A tie
    # yields multiple ids; an empty list means "no winner" (e.g. win_condition
    # "none"). Surfaced in the snapshot so the frontend can render a win/lose
    # result without parsing the log.
    winner_ids: list[str] = Field(default_factory=list)

    # Populated once the epilogue vote finalizes (phase == "ended"). None
    # before then (including during the pre-vote "results" phase). Rides the
    # snapshot so the final results screen (and a reconnecting client) can
    # render kept/destroyed outcomes without replaying the vote.
    epilogue_result: EpilogueResultSummary | None = None

    log: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _lift_legacy_rule_fields(cls, data: Any) -> Any:
        """Accept pre-``rules`` inputs (old persisted states, terse test builders).

        Top-level ``draw_count``/``win_condition``/``skip_predicate`` keys are
        routed into ``rules`` unless the input already carries an explicit
        ``rules`` value for them.
        """
        if not isinstance(data, dict):
            return data
        legacy = {
            "draw_count": "draw",
            "win_condition": "win_condition",
            "skip_predicate": "skip_predicate",
        }
        present = [k for k in legacy if k in data]
        if not present:
            return data
        data = dict(data)
        rules = data.get("rules")
        if isinstance(rules, Rules):
            rules = rules.model_dump()
        rules = dict(rules) if isinstance(rules, dict) else {}
        for key in present:
            rules.setdefault(legacy[key], data.pop(key))
        data["rules"] = rules
        return data

    @computed_field  # type: ignore[prop-decorator]
    @property
    def draw_count(self) -> int:
        """Cards drawn at start of each turn (reads ``rules.draw``)."""
        return self.rules.draw

    @computed_field  # type: ignore[prop-decorator]
    @property
    def win_condition(self) -> WinCondition:
        """The current win condition (reads ``rules.win_condition``)."""
        return self.rules.win_condition

    @property
    def skip_predicate(self) -> str | None:
        return self.rules.skip_predicate

    def turn_players(self) -> list[Player]:
        """Players who participate in the turn rotation.

        Spectators live in the separate ``spectators`` collection, not
        ``players``, so every entry here already participates in turns,
        dealing and scoring — this helper exists for callers (scoring, room
        setup) that read "the participating set" by name.
        """
        return list(self.players)

    def effective_turn_order(self) -> list[str]:
        """Return the turn rotation order: ``turn_order`` if set, else the
        default (``players``, in list order).

        This is the single read path ``advance_turn`` and neighbor-target
        resolution use to step through the rotation, so a still-unset
        ``turn_order`` (e.g. a game that hasn't started, or a state built
        without one) behaves exactly like the old players-list-order default.
        """
        return list(self.turn_order) if self.turn_order else [p.id for p in self.players]

    def active_player(self) -> Player:
        """Return the player whose turn it currently is."""
        return self.players[self.turn_index % len(self.players)]

    def get_player(self, player_id: str) -> Player:
        for p in self.players:
            if p.id == player_id:
                return p
        raise KeyError(f"Player {player_id!r} not found")

    def is_spectator(self, player_id: str) -> bool:
        """True if ``player_id`` is a watcher (present in ``spectators``)."""
        return any(s.id == player_id for s in self.spectators)

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

    def with_history_event(self, event: HistoryEvent) -> GameState:
        """Return a copy with one event appended using the next sequence id."""
        next_sequence = self.history_events[-1].sequence + 1 if self.history_events else 1
        return self.model_copy(
            update={
                "history_events": [
                    *self.history_events,
                    event.model_copy(update={"sequence": next_sequence}),
                ]
            }
        )

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
