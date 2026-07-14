"""board.rooms.room — one game session: GameState + ConnectionManager + turn enforcement.

Room owns an immutable GameState (replaced on each mutation) and serialises all
handle_action calls with an asyncio.Lock so concurrent WebSocket messages cannot
corrupt turn order. Play/pass require the active player's turn. Card authoring
(create_card/preview_card) is SETUP-ONLY; the only mid-game authoring is filling
in a blank as it is played (author-on-play, see _handle_play). Play runs the
agent interpretation graph via asyncio.to_thread (with a "brewing" broadcast),
applying resulting effects through the engine.

Turn model (auto-draw → play → end turn): drawing is AUTOMATIC. When a turn
begins (``_start_turn``) the server draws ``rules.draw`` card(s) for the new
active player — there is no client ``draw`` message. The player then ``play``s
a card OR ``pass``es / ``end_turn``s to end without playing. Either ending
advances the turn; the next player is auto-drawn to in the same way.

End game: there are TWO distinct end paths with distinct timing.

- Deck exhaustion: when a player draws the LAST card of the deck,
  ``_deck_exhausted`` latches. That player finishes their turn normally; only
  once their turn ends (``_advance_turn``) does the game end (Per the rules:
  the player who draws the last card completes their turn, then the game
  ends).
- Explicit end / live win condition: a card's ``end_game`` op sets
  ``rules.end_condition``, and ``set_win_condition`` can make
  ``evaluate_win_condition`` (via ``win_condition_met``) become true mid-play
  (e.g. a ``first_to`` threshold reached). Both are checked immediately after
  the triggering play resolves (``_handle_play``) as well as in
  ``_advance_turn`` (belt-and-suspenders for routes like ``_handle_pass`` that
  advance the turn without an intervening play) — the game ends RIGHT AWAY
  rather than waiting for the deck to run out.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

from engine.apply import apply_effect
from engine.compile import compile_card_plan
from engine.events import EventBus, GameEvent, HookContext
from engine.hooks import build_registry
from engine.history import append_history_event, record_draw, record_game_end
from engine.loop import advance_turn
from engine.scoring import evaluate_end_condition, evaluate_win_condition, resolve_end_of_game, win_condition_met
from models.card import MAX_ROOM_ART_BYTES
from models.effects import CustomNoteOp, EffectProgram, InteractionStep, OpsStep, ResolutionPlan, SnippetStep
from models.game_state import EndCondition, EpilogueCardOutcome, EpilogueResultSummary, GameState, Player, Spectator
from models.interactions import (
    CardPickInteraction,
    CardPickResponse,
    ChoiceInteraction,
    ChoiceResponse,
    ConfirmInteraction,
    ConfirmResponse,
    DrawingInteraction,
    DrawingResponse,
    InteractionDescriptor,
    InteractionOption,
    InteractionProgress,
    InteractionResponsePayload,
    NumberInteraction,
    NumberResponse,
    TextInteraction,
    TextResponse,
)
from models.ws_messages import CreateCardMsg
from board.rooms.interactions import PendingResolution
from board.rooms.connections import ConnectionManager
from board.rooms.deck import (
    BLANKS_PER_PLAYER,
    PREMADE_POOL_SIZE,
    build_premade_pool,
    finalize_deck,
)
from board.rooms.epilogue import EpilogueManager

logger = logging.getLogger(__name__)

# Cards dealt to each player's hand when the game starts.
STARTING_HAND_SIZE = 5

# Most hooks fired for one event per action (excess logs a skip line).
MAX_HOOKS_PER_EVENT = 8

# Cards each player must author during the setup phase before the game can start.
CARDS_TO_AUTHOR = BLANKS_PER_PLAYER

# Prefix stamped on the interpretation agent's in-character comment when it is
# appended to the persistent game log. Marks the line as the AI arbiter talking
# so players can tell it apart from the plain "X played Y" effect lines. Kept as
# a module constant so tests and any future styling share one source of truth.
AGENT_COMMENT_PREFIX = "🤖 "

# How long a reaction window stays open before the pending play auto-resolves.
# Long enough to read the pending card on a phone; tests monkeypatch it down.
# A reactor claiming the window (e.g. to answer a prompt_choice) restarts the
# timer so an abandoned follow-up can never wedge the room.
REACTION_WINDOW_SECONDS = 15.0


class PlanExecutionError(Exception):
    pass


class PlanPaused(Exception):
    def __init__(self, working_state: GameState, cursor: int, step: InteractionStep) -> None:
        self.working_state = working_state
        self.cursor = cursor
        self.step = step


@dataclass
class PendingPlay:
    """A play suspended while a reaction window is open.

    The reaction sibling of ``PendingResolution``: that one persists a play
    paused MID-execution at an interaction barrier (mutated working_state must
    survive a restart); this one suspends BEFORE any execution, so it is
    deliberately transient (a Room attribute, never GameState/store) — the
    pending card stays in the actor's hand until commit, and a restart just
    evaporates the window so the actor replays. ``window_id`` defeats timeout
    races — a stale timer or a late reaction sees a mismatched/cleared id and
    no-ops. The two suspensions are never live simultaneously: a window always
    closes (committing or negating the play) before its plan can pause on a
    barrier.
    """

    window_id: str
    actor_id: str
    card_id: str
    card: dict
    plan: ResolutionPlan  # already resolved; NOT re-resolved at commit
    chosen_player_id: str | None
    chosen_card_id: str | None
    eligible_ids: set[str]
    passed_ids: set[str] = field(default_factory=set)
    claimed_by: str | None = None  # reactor currently answering a prompt_choice
    deadline: float = 0.0  # epoch seconds (time.time())
    timer: asyncio.Task | None = None


class Room:
    """One game session. Thread-safe via asyncio.Lock."""

    def __init__(
        self,
        code: str,
        mode: str = "both",
        *,
        simple: bool = True,
        on_change: Callable[[Room], None] | None = None,
    ) -> None:
        self.code = code
        self.state: GameState = GameState(room_code=code, mode=mode)
        # When this room was created; set once and never mutated. Restored from
        # disk by FileRoomStore for a persisted room (see store._room_from_dict).
        self.created_at: datetime = datetime.now(UTC)
        self.connections: ConnectionManager = ConnectionManager()
        # Card art registry: card_id -> PNG data-URL. Deliberately a plain Room
        # attribute, NOT GameState — every mutation broadcasts the full state
        # snapshot to every client, so inline art would multiply every broadcast.
        # Cards carry only a has_art flag; clients fetch the bytes from
        # GET /rooms/{code}/cards/{card_id}/art (see board.app).
        self.card_art: dict[str, str] = {}
        # Running total of data-URL bytes in card_art, maintained by
        # _store_card_art to enforce MAX_ROOM_ART_BYTES without re-summing.
        self._card_art_bytes: int = 0
        self._lock: asyncio.Lock = asyncio.Lock()
        self._epilogue: EpilogueManager | None = None
        # Persistence callback fired after every serialized mutation; None keeps
        # the room ephemeral (the default, and the only behaviour in production).
        self.on_change = on_change
        # Whether to seed the pre-made pool from the deterministic point-only
        # simple deck (the basic no-AI game). Kept as an attribute so tests and
        # future modes can flip it; defaults True for the basic game.
        self._simple = simple
        # Per-turn bookkeeping for the auto-draw→play→end model. Reset at the
        # start of every turn (see _start_turn). ``_has_drawn`` records the
        # turn's auto-draw for the client snapshot; ``_deck_exhausted`` latches
        # once the last card is drawn so the game ends after the drawer
        # finishes their turn.
        self._has_drawn: bool = False
        self._plays_this_turn: int = 0
        self._deck_exhausted: bool = False
        # Per-room hook registry, a cache DERIVED from state.hooks (rebuilt when
        # the hook id list changes) — hooks are serialized state, so they survive
        # restarts and never leak across rooms. See engine.hooks.build_registry.
        self._hook_registry = None
        self._hook_fingerprint: tuple[str, ...] = ()
        # Card ids whose agent comment has already been appended to state.log this
        # session. A card that needs a target is resolved TWICE (resolve → prompt_choice
        # → follow-up play re-resolves), so this guards against double-logging the
        # arbiter comment for one played card. See _resolve_plan.
        self._comment_logged: set[str] = set()
        self._pending_resolution: PendingResolution | None = None
        self._interaction_timer: asyncio.Task | None = None
        # Card id of the play currently being interpreted/resolved (brewing),
        # or None. Set/cleared (try/finally) around the play branch in
        # _dispatch and checked BEFORE waiting on the lock in handle_action —
        # see handle_action for why the check must precede the lock.
        self._resolving_play: str | None = None
        # The play currently suspended behind an open reaction window, or None.
        # Transient by design — see PendingPlay.
        self._pending: PendingPlay | None = None

    # ── player management ──
    def add_player(self, player_id: str, name: str) -> None:
        """Append a real player to the immutable GameState (reassigns self.state)."""
        new_players = [*self.state.players, Player(id=player_id, name=name)]
        self.state = self.state.model_copy(update={"players": new_players})

    def add_spectator(self, player_id: str, name: str) -> None:
        """Append a spectator (late joiner) to the immutable GameState.

        Spectators live in ``state.spectators``, not ``players``: they take no
        turn, are never dealt/auto-drawn to, cannot author or play cards, and
        are excluded from win scoring — structurally, not by a guard. The join
        *policy* (who becomes a spectator) lives in :meth:`RoomManager.join`,
        which decides from the room's phase; this method just records it.
        """
        new_spectators = [*self.state.spectators, Spectator(id=player_id, name=name)]
        self.state = self.state.model_copy(update={"spectators": new_spectators})

    def get_player_ids(self) -> list[str]:
        """All ids that may open a WebSocket for this room: players + spectators."""
        return [p.id for p in self.state.players] + [s.id for s in self.state.spectators]

    def _is_spectator(self, player_id: str) -> bool:
        return self.state.is_spectator(player_id)

    def _is_host(self, player_id: str) -> bool:
        """True for the room's first joiner — mirrors the frontend's
        ``players[0]`` host convention. Players are only ever appended (see
        ``add_player``), so this is stable for the life of the room."""
        return bool(self.state.players) and self.state.players[0].id == player_id

    # ── turn helpers ──
    def _is_active_player(self, player_id: str) -> bool:
        if not self.state.players:
            return False
        idx = self.state.turn_index % len(self.state.players)
        return self.state.players[idx].id == player_id

    def _notify_change(self) -> None:
        """Fire the persistence hook, if wired. Callers hold the lock so the
        snapshot is consistent with the just-applied mutation."""
        if self.on_change is not None:
            self.on_change(self)

    # ── main dispatch ──

    # Game actions frozen while a play is being interpreted/resolved (brewing).
    # create_card stays in the set even though _dispatch rejects it outside
    # setup anyway: this check runs BEFORE the lock, so it keeps a doomed
    # message from queueing behind the play's long-held lock. Reaction
    # messages (pass_reaction, play + as_reaction) are deliberately exempt:
    # the reaction window only opens AFTER interpretation completes, so a
    # reaction sent mid-brew already bounces off the window machinery
    # ("The reaction window has closed" / claimed_by), and the exemption keeps
    # a reaction from racing the window-open broadcast at the tail of a play.
    FROZEN_WHILE_RESOLVING = frozenset({"start", "pass", "end_turn", "play", "create_card"})

    async def handle_action(self, player_id: str, msg) -> None:
        """Serialised entry point for all client messages.

        The play-resolution freeze is checked BEFORE waiting on the lock: the
        lock is held for a play's entire interpretation (including the
        threaded LLM call), so a game action arriving mid-brew can never
        observe ``_resolving_play`` from inside ``_dispatch`` — it would queue
        on the lock and execute against the post-resolution state, succeeding
        whenever the first play ended on a non-consuming path (prompt_choice,
        veto, reaction abort). Rejecting up front gives the sender an
        immediate error instead. The unlocked read is a benign race: a message
        that slips past just queues as before and lands in the normal
        turn/allowance gates.
        """
        if (
            self._resolving_play is not None
            and msg.type in self.FROZEN_WHILE_RESOLVING
            and not getattr(msg, "as_reaction", False)
        ):
            await self.connections.send(
                player_id,
                {"type": "error", "message": "Waiting for the current play to finish resolving"},
            )
            return
        async with self._lock:
            await self._dispatch(player_id, msg)
            self._notify_change()

    async def _dispatch(self, player_id: str, msg) -> None:
        mtype = msg.type
        # Spectators (joined after the game started) may observe but not act:
        # reject every game-mutating / authoring message. They still receive all
        # broadcasts (state, brewing, effect_applied, …) over their socket.
        # epilogue_vote is intentionally allowed through — spectators created no
        # cards, so a stray vote is harmless and the epilogue guard handles it —
        # but every write/authoring path is gated here.
        if self._is_spectator(player_id) and mtype in {
            "start",
            "pass",
            "end_turn",
            "play",
            "pass_reaction",
            "create_card",
            "preview_card",
            "interaction_response",
        }:
            await self.connections.send(player_id, {"type": "error", "message": "Spectators cannot take game actions"})
            return
        # Authoring gate: create_card/preview_card exist ONLY during setup
        # (each player writes their quota). The one mid-game authoring path is
        # playing a blank, which rides the `play` message (author-on-play).
        if mtype in {"create_card", "preview_card"} and self.state.phase != "setup":
            await self.connections.send(
                player_id, {"type": "error", "message": "Card authoring is only available during setup"}
            )
            return
        # Phase gate: once the game leaves "playing" (results/epilogue/ended —
        # e.g. an end_game card fired mid-deck), in-game actions must not run;
        # a stray play would re-trigger _end_game and double-apply end scoring.
        if mtype in {"pass", "end_turn", "play", "pass_reaction"} and self.state.phase != "playing":
            await self.connections.send(player_id, {"type": "error", "message": "The game is not in play"})
            return
        # Reaction routing comes BEFORE the active-player gates below:
        # reaction plays are made by non-active players, and normal turn actions
        # are frozen while a play is suspended behind an open window.
        if mtype == "play" and getattr(msg, "as_reaction", False):
            # A reaction resolves via the same LLM round-trip as a direct play,
            # so freeze the room for its duration too — otherwise the active
            # player's queued turn actions execute against post-resolution state
            # (the stale-queue race the direct-play freeze closes). Reaction
            # messages are exempt from the freeze themselves (they carry
            # as_reaction and are gated by the window), so this only blocks
            # non-reaction actions. Cleared unconditionally.
            self._resolving_play = msg.card_id
            try:
                await self._handle_reaction_play(player_id, msg)
            finally:
                self._resolving_play = None
            return
        if mtype == "pass_reaction":
            await self._handle_pass_reaction(player_id, msg)
            return
        if self._pending is not None and mtype in {"pass", "end_turn", "play"}:
            await self.connections.send(
                player_id, {"type": "error", "message": "Waiting for reactions to the pending play"}
            )
            return
        if self._pending_resolution is not None and mtype in {
            "start",
            "pass",
            "end_turn",
            "play",
        }:
            await self.connections.send(
                player_id,
                {"type": "error", "message": "Waiting for the current card interaction to finish"},
            )
            return
        if mtype == "start":
            await self._handle_start(player_id)
        elif mtype in ("pass", "end_turn"):
            if not self._is_active_player(player_id):
                await self.connections.send(player_id, {"type": "error", "message": "Not your turn"})
                return
            # Pass is only allowed when the player has nothing playable. If they
            # hold a playable card (any blank, or a card with an effect), they
            # must play it rather than pass.
            if not self._can_pass(player_id):
                await self.connections.send(
                    player_id, {"type": "error", "message": "You have a playable card — you cannot pass"}
                )
                return
            await self._handle_pass(player_id)
        elif mtype == "play":
            if not self._is_active_player(player_id):
                await self.connections.send(player_id, {"type": "error", "message": "Not your turn"})
                return
            # Freeze the room's game actions for the whole play (author-on-play
            # → veto → interpretation → execution → turn accounting); cleared
            # unconditionally so a crashing LLM call/plan can never leave the
            # room frozen. handle_action rejects against this flag pre-lock.
            self._resolving_play = msg.card_id
            try:
                await self._handle_play(player_id, msg)
            finally:
                self._resolving_play = None
        elif mtype == "create_card":
            await self._handle_create_card(player_id, msg)
        elif mtype == "preview_card":
            await self._handle_preview_card(player_id, msg)
        elif mtype == "interaction_response":
            await self._handle_interaction_response(player_id, msg)
        elif mtype == "epilogue_start":
            await self._handle_epilogue_start(player_id)
        elif mtype == "epilogue_vote":
            await self._handle_epilogue_vote(player_id, msg)
        elif mtype == "epilogue_done":
            await self._handle_epilogue_done(player_id)
        elif mtype == "epilogue_finalize":
            await self._handle_epilogue_finalize(player_id)
        else:
            await self.connections.send(player_id, {"type": "error", "message": f"Unknown message type: {mtype}"})

    # ── setup helpers ──
    def _authored_count(self, player_id: str) -> int:
        """Number of cards ``player_id`` has authored this game (setup step 3)."""
        return sum(
            1
            for c in self.state.cards.values()
            if (c.get("creator_id") if isinstance(c, dict) else getattr(c, "creator_id", None)) == player_id
        )

    def _setup_progress(self) -> dict[str, int]:
        """Map non-spectator player id -> authored-card count, for the client."""
        return {p.id: self._authored_count(p.id) for p in self.state.turn_players()}

    def _store_card_art(self, card_id: str, art: str) -> bool:
        """Store ``art`` in the out-of-band registry, enforcing the room budget.

        Returns False — art dropped, nothing stored — once the aggregate would
        exceed ``MAX_ROOM_ART_BYTES``: rooms are never evicted, so the registry
        needs a hard cap. Callers keep the card, just artless
        (``has_art: False``).
        """
        if self._card_art_bytes + len(art) > MAX_ROOM_ART_BYTES:
            return False
        self.card_art[card_id] = art
        self._card_art_bytes += len(art)
        return True

    def _absorb_card_art(self, cards: dict[str, dict]) -> dict[str, dict]:
        """Pop each card's transient ``art`` data-URL into ``self.card_art``.

        Cards re-entering from the RAG corpus surface their art under a
        transient ``art`` key (see deck._normalise_card). Art must never ride
        GameState (snapshots broadcast to every client), so this strips the key
        and stores the data-URL out-of-band — budget permitting; art that no
        longer fits is dropped and the card's ``has_art`` flag reset.
        """
        for cid, card in cards.items():
            art = card.pop("art", None)
            if art and not self._store_card_art(cid, art):
                card["has_art"] = False
        return cards

    # ── per-action handlers ──
    async def _handle_start(self, player_id: str) -> None:
        """Phase-aware game start (deck building happens in two steps).

        - From the **lobby**: build the shared PRE-MADE pool into ``state.cards``
          and enter ``phase="setup"``. Nothing is dealt yet. The pool is visible
          in the snapshot so every player can see the pre-made cards while
          authoring their own (step 3 of the game — build synergies).
        - From **setup**: gate on every non-spectator having authored
          ``CARDS_TO_AUTHOR`` cards; then finalise the deck (pre-made + authored
          + ``BLANKS_PER_PLAYER`` blanks per player), shuffle, deal
          ``STARTING_HAND_SIZE`` to each real player, enter ``phase="playing"``
          and begin the first turn.

        The setup→playing transition normally happens AUTOMATICALLY once every
        player finishes authoring (see ``_handle_create_card``); this manual
        entry is kept as a safety/fallback path (and still owns lobby→setup). A
        manual ``start`` that arrives after auto-start already fired lands in the
        ``else`` branch below as a harmless "Game already started" no-op.

        Deck building never requires a live external service; it runs in a thread
        since collection may touch the (in-memory) RAG store.
        """
        if self.state.phase == "lobby":
            await self._enter_setup()
        elif self.state.phase == "setup":
            await self._start_playing(player_id)
        else:
            await self.connections.send(player_id, {"type": "error", "message": "Game already started"})

    async def _enter_setup(self) -> None:
        """lobby → setup: seed the pre-made pool and open card authoring."""
        cards, pool = await asyncio.to_thread(
            build_premade_pool,
            count=PREMADE_POOL_SIZE,
            venue_mode=self.state.mode,
            simple=self._simple,
        )
        # Pre-made cards live in the registry AND (as ids) in the deck so the
        # setup UI can render "the deck so far". They're re-shuffled with the
        # authored + blank cards at finalisation. Cards kept from a prior game
        # may carry a transient "art" key (see deck._normalise_card) — absorb it
        # into the out-of-band registry before the dicts land in GameState.
        merged_cards = {**self._absorb_card_art(cards), **self.state.cards}
        self.state = self.state.model_copy(update={"phase": "setup", "cards": merged_cards, "deck": list(pool)})
        await self._broadcast_state()

    async def _start_playing(self, player_id: str | None = None, *, rng: random.Random | None = None) -> None:
        """setup → playing: gate on authoring, finalise deck, deal, begin play.

        ``player_id`` is the player who requested the manual start; it is used
        ONLY to address the "waiting on…" error when someone is still behind. On
        the AUTO-START path (called from ``_handle_create_card`` once everyone has
        finished authoring) there is no requesting player, so it is None and the
        gate below never fires (we only auto-start when nobody is behind).

        ``rng`` seeds the turn-order shuffle below — same injectable-``random.Random``
        idiom as ``finalize_deck`` — so tests can pin a specific order; production
        callers leave it None for a fresh shuffle each game.
        """
        players = list(self.state.players)
        dealt_to = players

        # Gate: every real player must have authored the required number of cards.
        behind = [p for p in dealt_to if self._authored_count(p.id) < CARDS_TO_AUTHOR]
        if behind:
            names = ", ".join(self._name(p.id) for p in behind)
            if player_id is not None:
                await self.connections.send(
                    player_id,
                    {"type": "error", "message": f"Waiting on {names} to author {CARDS_TO_AUTHOR} cards"},
                )
            return

        # The pre-made pool ids are the current deck; authored cards are the
        # non-blank, non-premade registry entries created by players.
        premade_ids = list(self.state.deck)
        premade_set = set(premade_ids)
        authored_ids = [
            cid
            for cid, c in self.state.cards.items()
            if cid not in premade_set
            and not (c.get("blank") if isinstance(c, dict) else getattr(c, "blank", False))
            and (c.get("creator_id") if isinstance(c, dict) else getattr(c, "creator_id", None))
            in {p.id for p in dealt_to}
        ]

        blank_cards, deck = await asyncio.to_thread(
            finalize_deck,
            premade_ids,
            authored_ids,
            len(dealt_to),
            blanks_per_player=BLANKS_PER_PLAYER,
        )

        # Deal starting hands off the top of the shuffled deck.
        hands: dict[str, list[str]] = {p.id: list(p.hand) for p in dealt_to}
        for _ in range(STARTING_HAND_SIZE):
            for p in dealt_to:
                if not deck:
                    break
                hands[p.id].append(deck.pop(0))

        new_players = [p.model_copy(update={"hand": hands[p.id]}) if p.id in hands else p for p in players]
        merged_cards = {**self.state.cards, **blank_cards}
        # Seed the explicit turn rotation from the real players who made it
        # into this game, shuffled — IRL the player right of the dealer
        # starts; online we randomize instead of always starting the host.
        rng = rng or random.Random()
        turn_order = [p.id for p in dealt_to]
        rng.shuffle(turn_order)
        # active_player() reads players[turn_index], not turn_order directly,
        # so turn_index must point at turn_order[0] or the shuffle above would
        # only reorder who goes 2nd/3rd/... while the host still always opens.
        turn_index = next((i for i, p in enumerate(new_players) if p.id == turn_order[0]), 0) if turn_order else 0
        self.state = self.state.model_copy(
            update={
                "phase": "playing",
                "cards": merged_cards,
                "deck": deck,
                "players": new_players,
                "turn_order": turn_order,
                "turn_index": turn_index,
                "turn_number": 1,
            }
        )
        # Begin the first player's turn — _start_turn auto-draws for them, so
        # the first player's turn starts fully dealt like every later turn.
        if self.state.players:
            await self._start_turn(self.state.active_player().id)
        await self._broadcast_state()

    async def dev_autofill_authoring(self) -> None:
        """DEV shortcut: fast-forward lobby/setup straight to ``phase="playing"``.

        Enters setup if still in the lobby, then authors placeholder cards for every
        non-spectator until each has met ``CARDS_TO_AUTHOR`` — the last authored card
        trips the existing auto-start into playing. Raises ``ValueError`` if the game
        has already started (the endpoint maps that to a 409).

        We take ``self._lock`` ourselves and call the internal (already-unlocked)
        handlers directly: this is invoked from a REST endpoint, not through
        ``handle_action``, so we must reproduce its single-lock serialization guarantee
        without re-entering the lock via ``handle_action``.
        """
        async with self._lock:
            if self.state.phase not in ("lobby", "setup"):
                raise ValueError("game already started")
            if self.state.phase == "lobby":
                await self._enter_setup()
            for player in self.state.turn_players():
                pid = player.id
                i = 0
                while self._authored_count(pid) < CARDS_TO_AUTHOR:
                    await self._handle_create_card(
                        pid, CreateCardMsg(title=f"dev-{pid}-{i}", description="gain 1 point")
                    )
                    i += 1
            self._notify_change()

    # ── turn lifecycle (auto-draw → play → end turn → advance) ──
    async def _start_turn(self, player_id: str) -> None:
        """Begin ``player_id``'s turn: reset per-turn bookkeeping, auto-draw
        their ``rules.draw`` card(s), broadcast the fresh snapshot.

        Every turn — including the very first at the setup→playing transition —
        starts here, so the auto-draw is uniform. _start_turn is only ever
        called outside interaction barriers / reaction windows (from
        ``_start_playing`` and ``_advance_turn``), so the draw can never
        interleave with a suspended play. End-of-game timing is handled in
        ``_advance_turn`` (once the deck is exhausted the drawer finishes,
        then the game ends).
        """
        self._has_drawn = False
        self._plays_this_turn = 0
        await self._emit_hooks(GameEvent.ON_TURN_START, player_id)
        await self._auto_draw(player_id)
        await self._broadcast_state()

    async def _auto_draw(self, player_id: str) -> None:
        """Server-side draw of ``rules.draw`` card(s) at turn start.

        Drawing is automatic — the protocol has no client ``draw`` message. A
        draw rule of 0 (e.g. Uno-style house rules) or an empty deck satisfies
        the draw step without touching the deck. When the end condition is met
        right after drawing (deck_empty being the classic case) the end latches
        so the game ends after this player finishes their turn.
        """
        self._has_drawn = True
        amount = self.state.rules.draw
        if amount <= 0 or not self.state.deck:
            return
        actual = self._draw_cards(player_id, amount, source="turn")
        await self._emit_hooks(GameEvent.ON_DRAW_STEP, player_id)
        if evaluate_end_condition(self.state):
            # Met at draw time (classically: the last card was just drawn) —
            # the game ends when this player's turn ends.
            self._deck_exhausted = True
        noun = "card" if actual == 1 else "cards"
        await self._log_and_broadcast(f"{self._name(player_id)} drew {actual} {noun}")

    def _draw_cards(self, player_id: str, count: int, *, source: str) -> int:
        """Move up to ``count`` cards from the top of the deck into a hand (in place
        on self.state via immutable copy). Stops early if the deck runs out."""
        n = min(count, len(self.state.deck))
        if n <= 0:
            return 0
        drawn, rest = self.state.deck[:n], self.state.deck[n:]
        new_players = [
            p.model_copy(update={"hand": [*p.hand, *drawn]}) if p.id == player_id else p for p in self.state.players
        ]
        self.state = record_draw(
            self.state.model_copy(update={"deck": rest, "players": new_players}),
            player_id,
            n,
            source=source,
        )
        return n

    async def _advance_turn(self) -> None:
        """End the current turn: end the game if it's over, else advance to the
        next player and start their turn.

        Reuses ``engine.loop.advance_turn`` so turn_order, skip-next, extra-turn
        and any registered skip predicate are all honoured — those flags are set
        by the reducers during a play's apply_effect. Runs under the caller's
        lock, so advance is a single serialized operation with no interleaving.

        Three end triggers, all routed to ``_end_game`` here:

        - ``_deck_exhausted``: the last card was drawn this game. Its drawer
          finishes their turn and THEN the game ends here — matching the rule
          "the last card is drawn, that player finishes their turn, then the
          game ends".
        - a non-deferred ``rules.end_condition`` is met (an ``end_game`` op set
          {type: "now"}, or points_reached/empty_hand fired). Normally already
          handled immediately in ``_handle_play``; kept here too as a defensive
          catch-all for any other route (e.g. ``_handle_pass``).
        - ``win_condition_met(state)``: a live win condition (e.g. ``first_to``
          a threshold) was satisfied. Same defensive-catch-all reasoning.
        """
        if not self.state.players:
            return
        await self._emit_hooks(GameEvent.ON_TURN_END, self.state.active_player().id)
        if self._deck_exhausted or self._end_now() or win_condition_met(self.state):
            await self._end_game()
            return
        self.state = advance_turn(self.state)
        await self._start_turn(self.state.active_player().id)

    def _end_now(self) -> bool:
        """A met end condition that does NOT defer to the drawer-finishes-turn
        timing (everything except deck_empty ends play immediately)."""
        return self.state.rules.end_condition.type != "deck_empty" and evaluate_end_condition(self.state)

    def _hook_bus(self) -> EventBus:
        fingerprint = tuple(h.id for h in self.state.hooks)
        if self._hook_registry is None or fingerprint != self._hook_fingerprint:
            self._hook_registry = build_registry(self.state)
            self._hook_fingerprint = fingerprint
        return EventBus(self._hook_registry, max_hooks=MAX_HOOKS_PER_EVENT)

    async def _emit_hooks(self, event: GameEvent, actor_id: str, *, card_id: str | None = None) -> None:
        """Fire registered hooks for ``event`` (off-loop: each fire is a sandbox
        subprocess) and adopt the resulting state. No-op when nothing subscribes."""
        bus = self._hook_bus()
        if not self._hook_registry.hooks_for_event(str(event)):
            return
        ctx = HookContext(event=event, actor_id=actor_id, card_id=card_id)
        self.state = await asyncio.to_thread(bus.emit, event, self.state, ctx)

    async def _check_play_veto(self, player_id: str, card_id: str, card) -> str | None:
        """Fire ON_VALIDATE_PLAY hooks; return the first veto reason, or None.

        Validation hooks are pure predicates: a hook's snippet calls
        ``game.reject_play(reason)`` to veto; every other recorded op is
        DISCARDED. Hook errors log and count as allow — a broken rule must
        never brick the game. The vetoed card stays in hand and the turn is
        not consumed.
        """
        specs = [h for h in self.state.hooks if h.event == str(GameEvent.ON_VALIDATE_PLAY)]
        if not specs:
            return None
        from config import get_settings

        if not get_settings().snippet_execution_enabled:
            return None
        from engine.sandbox.revalidate import extract_veto
        from engine.sandbox.runner import SnippetExecutionError, execute_snippet

        attributes = dict(card.get("attributes") or {}) if isinstance(card, dict) else {}
        ctx_dict = {
            "actor_id": player_id,
            "event": str(GameEvent.ON_VALIDATE_PLAY),
            "card_id": card_id,
            "amount": None,
            "card_title": self._card_title(card),
            "card_attributes": attributes,
        }
        state_dict = json.loads(self.state.model_dump_json())
        for spec in specs[:MAX_HOOKS_PER_EVENT]:
            try:
                raw_ops = await asyncio.to_thread(execute_snippet, spec.code, state_dict, ctx_dict)
            except SnippetExecutionError as exc:
                await self._log_and_broadcast(f"[hook error] {spec.source_card_id}: {exc}")
                continue
            reason = extract_veto(raw_ops)
            if reason is not None:
                return reason
        return None

    async def _apply_cannot_play(self, player_id: str) -> None:
        """rules.cannot_play fallback: a player left without a legal play draws.

        Exhaustively validating every card in hand would cost a sandbox run per
        card per rule, so the pragmatic trigger is "the vetoed card was their
        only card": then cannot_play.draw fires (default 1).
        """
        hand = self.state.get_player(player_id).hand
        if len(hand) > 1 or not self.state.deck:
            return
        amount = int((self.state.rules.cannot_play or {}).get("draw", 0) or 0)
        if amount <= 0:
            return
        actual = self._draw_cards(player_id, amount, source="cannot_play")
        if self.state.rules.end_condition.type == "deck_empty" and not self.state.deck:
            self._deck_exhausted = True
        await self._log_and_broadcast(f"{self._name(player_id)} cannot play and draws {actual}")
        await self._broadcast_state()

    def _name(self, player_id: str) -> str:
        """Human-readable display name for a player id (falls back to the id)."""
        for p in self.state.players:
            if p.id == player_id:
                return p.name
        return player_id

    def _card_title(self, card) -> str:
        """A card's display title (falls back to a generic label)."""
        if isinstance(card, dict):
            return card.get("title") or "a card"
        return getattr(card, "title", None) or "a card"

    def _format_score_deltas(self, deltas: dict[str, int]) -> str:
        """Render {player_id: change} as "Alice +5, Bob -2", in player order.

        Zero/absent changes are omitted; returns "" if nothing changed.
        """
        parts = []
        for p in self.state.players:
            change = deltas.get(p.id, 0)
            if change:
                parts.append(f"{p.name} {'+' if change > 0 else ''}{change}")
        return ", ".join(parts)

    def _describe_play(self, player_id: str, card, before: dict[str, int]) -> str:
        """Build a human-readable play log line with the resulting score deltas.

        e.g. "Alice played Gain 5 Points (Alice +5)" or, for a multi-target
        card, "Bob played Everyone Else Loses 2 (Alice -2, Carol -2)". Replaces
        the old raw ``Played <card_id>`` line so players can actually follow what
        happened.
        """
        deltas = {p.id: p.score - before.get(p.id, p.score) for p in self.state.players}
        line = f"{self._name(player_id)} played {self._card_title(card)}"
        formatted = self._format_score_deltas(deltas)
        if formatted:
            line += f" ({formatted})"
        return line

    async def _handle_pass(self, player_id: str) -> None:
        """Active player ends their turn without playing a card."""
        await self._log_and_broadcast(f"{self._name(player_id)} passed")
        await self._advance_turn()

    async def _end_game(self) -> None:
        """Resolve end-of-game scoring, compute winners, then show results.

        Sequence (the deck was exhausted and the drawer finished their turn):

        1. ``resolve_end_of_game`` applies any kept-in-hand / in-play end-of-game
           card effects (e.g. "worth 10 points if you keep it") so final scores
           reflect what players held at the buzzer. Each application is logged
           BEFORE the winner announcement, so the score jump is never silent.
        2. ``evaluate_win_condition`` computes ``winner_ids`` from those final
           scores (default: highest points). Winners are stored on the state and
           logged so ALL connected players see the result.
        3. We land on ``phase="results"`` (final scores + full history) rather
           than opening the epilogue immediately — the host explicitly advances
           into voting via ``epilogue_start`` (see ``_handle_epilogue_start``),
           so players see the results screen BEFORE voting. If there are no
           real players to vote (e.g. an all-spectator remnant) there is
           nothing to advance for, so we skip straight to ``ended``.
        """
        actor = self.state.active_player().id if self.state.players else ""
        await self._emit_hooks(GameEvent.ON_GAME_END, actor)
        self.state, applications = resolve_end_of_game(self.state)
        for application in applications:
            line = f"Game end: {application.holder_name}'s '{application.card_title}'"
            formatted = self._format_score_deltas(application.deltas)
            if formatted:
                line += f" ({formatted})"
            await self._log_and_broadcast(line)
        winners = self.state.winner_override or evaluate_win_condition(self.state)
        self.state = record_game_end(self.state, list(winners), actor_id=actor or None, source="room")
        if winners:
            names = [self.state.get_player(w).name for w in winners]
            log_line = f"Game over! Winner(s): {', '.join(names)}"
        else:
            log_line = "Game over! No winner."
        self.state = self.state.model_copy(update={"winner_ids": winners})
        await self._log_and_broadcast(log_line)
        next_phase = "results" if self.state.turn_players() else "ended"
        update: dict = {"phase": next_phase, "winner_override": []}
        if self.state.rules.end_condition.type == "now":
            update["rules"] = self.state.rules.model_copy(update={"end_condition": EndCondition()})
        self.state = self.state.model_copy(update=update)
        await self._broadcast_state()

    async def dev_force_end_game(self) -> None:
        """DEV shortcut: end an in-progress game NOW via the real end-game path.

        Runs the exact ``_end_game`` sequence (kept-card scoring → winners →
        results, or ``ended`` when no real players remain), so behaviour
        matches a genuine deck-exhaustion end game. Raises ``ValueError`` if the
        game is not playing (the endpoint maps that to a 409).

        We take ``self._lock`` ourselves: this is invoked from a REST endpoint,
        not through ``handle_action``, so we must reproduce its single-lock
        serialization guarantee without re-entering the lock via ``handle_action``.
        """
        async with self._lock:
            if self.state.phase != "playing":
                raise ValueError("game is not in progress")
            await self._end_game()
            self._notify_change()

    def _is_blank(self, card) -> bool:
        """True if ``card`` is an un-authored blank (blank flag still set)."""
        if isinstance(card, dict):
            return bool(card.get("blank"))
        return bool(getattr(card, "blank", False))

    def _is_authored_card(self, card) -> bool:
        """True if ``card`` belongs in the epilogue vote pool.

        Authored this game OR kept from a previous game (a RAG re-entry) —
        never a shipped seed card, never an un-authored blank. Driven by the
        ``origin`` field stamped at creation/deal time (see deck._normalise_card,
        deck._make_blank_card, Room._handle_create_card, Room._handle_play).
        """
        if isinstance(card, dict):
            return card.get("origin") == "authored"
        return getattr(card, "origin", None) == "authored"

    def _card_is_playable(self, card) -> bool:
        """True if a card in hand can meaningfully be played.

        A card is playable if it is a blank (blanks are ALWAYS playable — they're
        authored on play), OR it compiles to a non-empty plan, OR it carries
        free text the LLM could interpret. In practice nearly every card is
        playable; the only truly inert card is an empty, canonical-less,
        description-less entry. This deliberately errs toward "playable" so we
        never force a pass when the player actually has options.
        """
        if self._is_blank(card):
            return True
        # Reaction cards are only legal inside a reaction window — a hand of
        # nothing but reactions must not deadlock the pass gate.
        if self._is_reaction_card(card):
            return False
        card_dict = card if isinstance(card, dict) else card.model_dump()
        plan = compile_card_plan(card_dict)
        if plan is not None and plan.steps:
            return True
        # A free-text card (description present) can still be interpreted/played.
        description = card_dict.get("description") or ""
        return bool(description.strip())

    def _is_reaction_card(self, card) -> bool:
        """True when the card's canonical trigger is "on_reaction" — playable
        ONLY during another player's play, never on your own play step."""
        if isinstance(card, dict):
            canonical = card.get("canonical") or {}
            trigger = card.get("trigger") or (canonical.get("trigger") if isinstance(canonical, dict) else None)
        else:
            canonical = getattr(card, "canonical", None)
            trigger = getattr(card, "trigger", None) or getattr(canonical, "trigger", None)
        return trigger == str(GameEvent.ON_REACTION)

    def _is_uncounterable(self, card) -> bool:
        """True when the card carries an ``uncounterable`` flag (properties are
        authored at creation; attributes are written by set_card_attribute)."""
        if not isinstance(card, dict):
            return False
        for bag_key in ("properties", "attributes"):
            bag = card.get(bag_key)
            if isinstance(bag, dict) and bag.get("uncounterable"):
                return True
        return False

    @staticmethod
    def _public_mechanical_reason(reason: object, *, fallback: str) -> str:
        """Return a bounded, single-line diagnostic safe for shared snapshots."""
        text = " ".join(str(reason).split()) if reason else fallback
        text = re.sub(r"(?:/[A-Za-z0-9_.-]+){2,}", "[path]", text)
        return text[:240] or fallback

    def _set_card_mechanical_status(
        self,
        card_id: str,
        status: str,
        correlation_id: str,
        reason: str | None = None,
    ) -> None:
        card = self.state.cards.get(card_id)
        if not isinstance(card, dict):
            return
        updated = {
            **card,
            "mechanical_status": status,
            "mechanical_reason": reason,
            "correlation_id": correlation_id,
        }
        self.state = self.state.model_copy(update={"cards": {**self.state.cards, card_id: updated}})
        self._notify_change()

    def _can_pass(self, player_id: str) -> bool:
        """True if the active player may end their turn WITHOUT playing.

        Pass is only offered when the player holds NO playable card — if they can
        play something (including any blank), they must. Non-active players and
        spectators can never pass.
        """
        if not self._is_active_player(player_id) or self._is_spectator(player_id):
            return False
        hand = self.state.get_player(player_id).hand
        return not any(self._card_is_playable(self.state.cards.get(cid, {})) for cid in hand)

    def _play_destination(self, card) -> str:
        """Return the zone a played card lands in: "center" | "in_play" | "discard".

        Schema v2 (data/eval/CANONICAL_SPEC.md): placement "center" = game-wide
        modifier on the shared table; "player" = modifier that stays in front of
        the affected player (in_play); "discard" = one-shot. Legacy v1 canonicals
        (placement "self" + timing "modifier") persist in old room state and RAG
        payloads, so the v1 branch stays.
        """
        canonical = card.get("canonical") if isinstance(card, dict) else getattr(card, "canonical", None)
        if canonical is None:
            # No canonical (blanks, plain point cards): resolve-and-discard.
            return "discard"
        placement = canonical.get("placement") if isinstance(canonical, dict) else getattr(canonical, "placement", None)
        timing = canonical.get("timing") if isinstance(canonical, dict) else getattr(canonical, "timing", None)
        if placement == "center":
            return "center"
        if placement == "player" and timing != "immediate":
            return "in_play"
        if placement == "self" and timing == "modifier":  # legacy v1
            return "in_play"
        return "discard"

    def _placement_owner(self, card, ctx: HookContext) -> str:
        """Which player an in_play (placement "player") card sits in front of:
        the chosen target when the play had one, else the actor. Legacy
        placement "self" always attaches to the actor."""
        canonical = card.get("canonical") if isinstance(card, dict) else getattr(card, "canonical", None)
        placement = canonical.get("placement") if isinstance(canonical, dict) else getattr(canonical, "placement", None)
        if placement == "player":
            return ctx.chosen_player_id or ctx.actor_id
        return ctx.actor_id

    async def _resolve_plan(
        self,
        card_id: str,
        card,
        actor_id: str | None = None,
        *,
        correlation_id: str,
    ) -> ResolutionPlan:
        title = card["title"] if isinstance(card, dict) else getattr(card, "title", "")
        description = card["description"] if isinstance(card, dict) else getattr(card, "description", "")
        creator_id = card.get("creator_id") if isinstance(card, dict) else getattr(card, "creator_id", None)

        compiled = compile_card_plan(card if isinstance(card, dict) else card.model_dump())
        if compiled is not None and compiled.steps:
            return compiled

        from agent.contract import InterpretResult
        from agent.runtime import run_agent

        await self.connections.broadcast({"type": "brewing", "card_id": card_id})
        try:
            # card_art is a side-channel arg: the drawing lives in Room.card_art,
            # never in the GameState handed to the agent.
            result: InterpretResult = await asyncio.to_thread(
                run_agent,
                title,
                description,
                self.state,
                actor_id,
                creator_id=creator_id,
                card_id=card_id,
                card_art=self.card_art.get(card_id),
            )
        except Exception:
            logger.exception("run_agent failed for %s; using deterministic fallback", card_id)
            result = InterpretResult(verdict="invalid", comment="", persona_action="none")

        await self.connections.broadcast(
            {
                "type": "card_interpreted",
                "card_id": card_id,
                "program": str(result.program) if result.program is not None else None,
                "snippet": getattr(result.snippet, "code", None),
                "verdict": result.verdict,
                "comment": result.comment,
                "mechanical_status": "pending" if result.verdict == "ok" else "fallback",
                "mechanical_reason": (
                    None if result.verdict == "ok" else "The arbiter could not produce an executable effect."
                ),
                "correlation_id": correlation_id,
            }
        )

        await self._log_agent_comment(card_id, result.comment)

        canonical = self._canonicalize_interpretation(result)
        if (
            canonical
            and isinstance(self.state.cards.get(card_id), dict)
            and not self.state.cards[card_id].get("canonical")
        ):
            merged_card = {**self.state.cards[card_id], **canonical, "verdict": result.verdict}
            self.state = self.state.model_copy(update={"cards": {**self.state.cards, card_id: merged_card}})

        plan = result.to_plan()
        if result.verdict == "ok" and plan.steps:
            return plan

        self._set_card_mechanical_status(
            card_id,
            "fallback",
            correlation_id,
            "The arbiter could not produce an executable effect.",
        )
        note = title or "Card"
        return ResolutionPlan(steps=[OpsStep(ops=[CustomNoteOp(note=f"Played {note} (no mechanical effect)")])])

    def _canonicalize_interpretation(self, result) -> dict:
        """Build the structured ``canonical`` payload for an interpreted card.

        Programs serialize their live ops; a triggered snippet becomes a
        register_hook authoring op (single pipeline); an immediate snippet is
        carried as canonical["sandbox"] for the play path. A snippet the agent
        marks trigger="on_reaction" makes the card a REACTION: its canonical
        records the trigger (so the room recognises it) and its code runs when
        the card is played into a reaction window. Cards with neither
        contribute nothing (fall back to the LLM next time).
        """
        plan = result.to_plan()
        canonical: dict = {}
        snippet = getattr(result, "snippet", None)
        if snippet is not None and getattr(snippet, "trigger", None) == str(GameEvent.ON_REACTION):
            canonical["trigger"] = str(GameEvent.ON_REACTION)
        if not plan.steps:
            return {"canonical": canonical} if canonical else {}
        canonical["steps"] = [step.model_dump() for step in plan.steps]
        ops = [op.model_dump() for step in plan.steps if isinstance(step, OpsStep) for op in step.ops]
        snippets = [step for step in plan.steps if isinstance(step, SnippetStep)]
        if ops:
            canonical["ops"] = ops
        if len(snippets) == 1 and isinstance(plan.steps[-1], SnippetStep):
            canonical["sandbox"] = snippets[0].code
        return {"canonical": canonical}

    async def _execute_plan(
        self,
        base_state: GameState,
        plan: ResolutionPlan,
        ctx: HookContext,
        card,
        *,
        start_cursor: int = 0,
        working_state: GameState | None = None,
        zone_owner: str | None = None,
    ) -> GameState:
        from config import get_settings
        from engine.sandbox.revalidate import apply_snippet_diff
        from engine.sandbox.runner import execute_snippet

        card_id = ctx.card_id or ""
        if working_state is None:
            destination = self._play_destination(card)
            working = base_state.move_card(
                card_id,
                "hand",
                destination,
                # zone_owner = whose hand the card leaves (differs from
                # ctx.actor_id only for a redirected reaction, where the effect
                # runs as the reactor but the card was in the actor's hand).
                from_player_id=zone_owner or ctx.actor_id,
                to_player_id=self._placement_owner(card, ctx) if destination == "in_play" else ctx.actor_id,
            )
        else:
            working = working_state
        rng = random.Random()
        ctx_dict = {
            "actor_id": ctx.actor_id,
            "event": str(ctx.event),
            "card_id": ctx.card_id,
            "amount": ctx.amount,
            # Snippet diffs reject "chooser" targets (no prompt_choice flow), so
            # sandbox code targeting a chosen player reads this and addresses
            # them as "id:" + ctx["chosen_player_id"].
            "chosen_player_id": ctx.chosen_player_id,
            "chosen_card_id": ctx.chosen_card_id,
            "interactions": ctx.interactions,
            "interaction_refs": ctx.interaction_refs,
        }
        for cursor, step in enumerate(plan.steps[start_cursor:], start=start_cursor):
            if isinstance(step, InteractionStep):
                raise PlanPaused(working, cursor, step)
            bus = EventBus(build_registry(working), max_hooks=MAX_HOOKS_PER_EVENT)
            if isinstance(step, OpsStep):
                working = apply_effect(working, EffectProgram(ops=step.ops), ctx, bus=bus, rng=rng)
                continue
            if not get_settings().snippet_execution_enabled:
                raise PlanExecutionError("snippet execution is disabled")
            state_dict = json.loads(working.model_dump_json())
            raw_ops = await asyncio.to_thread(execute_snippet, step.code, state_dict, ctx_dict)
            working = apply_snippet_diff(working, raw_ops, ctx, origin="play", bus=bus, rng=rng)
        return working

    async def _handle_play(self, player_id: str, msg) -> None:
        """Resolve the played card to an EffectProgram, apply it, advance turn.

        Blank cards are AUTHORED ON PLAY. When the played card is blank, the
        client's FIRST play for that card_id carries the authored ``title`` and
        ``description``. We PERSIST those onto the card (clearing the blank flag,
        setting creator_id=player_id) BEFORE resolving — this ordering matters
        because a card that needs a target replies with prompt_choice and the
        follow-up play re-runs this handler with only card_id + the choice (no
        title/description). By the time that follow-up arrives the card is already
        a real, authored card in state.cards, so re-resolution behaves identically.

        Resolution prefers a deterministic stored plan and falls back to the LLM
        then to a CustomNoteOp, so a play never silently no-ops.
        """
        if self.state.rules.play <= 0:
            await self.connections.send(
                player_id, {"type": "error", "message": "Playing cards is disabled by the current rules"}
            )
            return
        if self._plays_this_turn >= self.state.rules.play:
            await self.connections.send(player_id, {"type": "error", "message": "No plays left this turn"})
            return
        card_id = msg.card_id
        card = self.state.cards.get(card_id)
        if card is None:
            await self.connections.send(player_id, {"type": "error", "message": f"Card {card_id} not found"})
            return
        if self._is_reaction_card(card):
            # Reactions are only legal inside a reaction window. Card stays in
            # hand, turn not consumed.
            await self.connections.send(
                player_id,
                {
                    "type": "error",
                    "message": (
                        f"{self._card_title(card)} is a reaction — "
                        "it can only be played when another player plays a card"
                    ),
                },
            )
            return

        # Author-on-play: a blank must be filled in before it can be resolved.
        if self._is_blank(card):
            title = (getattr(msg, "title", None) or "").strip()
            description = (getattr(msg, "description", None) or "").strip()
            if not title or not description:
                # Guard: a blank reached play with no authored content (shouldn't
                # happen from the UI). Don't resolve an empty card — the turn
                # is not consumed, so the player can retry with content.
                await self.connections.send(
                    player_id,
                    {"type": "error", "message": "A blank card must be given a title and description to play"},
                )
                return
            art = msg.art
            if art and not self._store_card_art(card_id, art):
                art = None
                await self.connections.send(
                    player_id,
                    {"type": "error", "message": "This room's art storage is full — card played without art"},
                )
            authored = {
                **card,
                "title": title,
                "description": description,
                "creator_id": player_id,
                "origin": "authored",
                "has_art": bool(art),
            }
            authored.pop("blank", None)
            merged = {**self.state.cards, card_id: authored}
            self.state = self.state.model_copy(update={"cards": merged})
            card = authored

        veto = await self._check_play_veto(player_id, card_id, card)
        if veto is not None:
            correlation_id = str(uuid.uuid4())
            reason = self._public_mechanical_reason(veto, fallback="A table rule rejected this play.")
            self._set_card_mechanical_status(card_id, "rejected", correlation_id, reason)
            logger.info(
                "card resolution rejected correlation_id=%s card_id=%s reason=%s", correlation_id, card_id, reason
            )
            await self.connections.send(player_id, {"type": "error", "message": f"Play rejected: {veto}"})
            await self._log_and_broadcast(
                f"[rule] {self._name(player_id)}'s {self._card_title(card)} was rejected: {veto}"
            )
            await self._apply_cannot_play(player_id)
            return

        title = card["title"] if isinstance(card, dict) else getattr(card, "title", "")
        correlation_id = str(uuid.uuid4())
        self._set_card_mechanical_status(card_id, "pending", correlation_id)
        await self._broadcast_state()
        plan = await self._resolve_plan(
            card_id,
            card,
            actor_id=player_id,
            correlation_id=correlation_id,
        )
        # Re-check after resolution: a blank authored on play may have been
        # canonicalized by the LLM as a reaction. Abort the same way — the card
        # hasn't moved zones, and it is now persisted in hand as a real
        # reaction card for future windows.
        persisted = self.state.cards.get(card_id, card)
        if self._is_reaction_card(persisted):
            await self.connections.send(
                player_id,
                {
                    "type": "error",
                    "message": (
                        f"{self._card_title(persisted)} turned out to be a reaction — "
                        "it stays in your hand until another player plays a card"
                    ),
                },
            )
            return
        chosen_player_id = getattr(msg, "chosen_player_id", None)
        chosen_card_id = getattr(msg, "chosen_card_id", None)
        valid_player_ids = {p.id for p in self.state.players}
        valid_card_ids = set(self.state.cards_in_play()) | set(self.state.get_player(player_id).hand)
        ops = plan.operations()
        needs_player_choice = any(
            getattr(op, field, None) in ("chooser", "target_player")
            for op in ops
            for field in ("target", "from_target", "to_target")
        )
        needs_card_choice = any(getattr(op, "card_target", None) == "chosen_card" for op in ops)

        if needs_player_choice and chosen_player_id is None:
            await self.connections.send(
                player_id,
                {
                    "type": "prompt_choice",
                    "card_id": card_id,
                    "prompt": f"Choose a target player for {title}",
                    "choices": [{"player_id": p.id, "name": p.name} for p in self.state.players],
                },
            )
            return
        if chosen_player_id is not None and chosen_player_id not in valid_player_ids:
            await self.connections.send(
                player_id,
                {"type": "error", "message": f"Invalid target player: {chosen_player_id}"},
            )
            return
        if needs_card_choice and chosen_card_id is None:
            await self.connections.send(
                player_id,
                {
                    "type": "prompt_choice",
                    "card_id": card_id,
                    "prompt": f"Choose a target card for {title}",
                    "choices": [
                        {
                            "card_id": cid,
                            "name": (
                                self.state.cards[cid].get("title", cid)
                                if isinstance(self.state.cards.get(cid), dict)
                                else getattr(self.state.cards.get(cid), "title", cid)
                            ),
                        }
                        for cid in valid_card_ids
                    ],
                },
            )
            return
        if needs_card_choice and chosen_card_id not in valid_card_ids:
            await self.connections.send(
                player_id,
                {"type": "error", "message": f"Invalid target card: {chosen_card_id}"},
            )
            return

        ctx = HookContext(
            event=GameEvent.ON_PLAY,
            actor_id=player_id,
            card_id=card_id,
            chosen_player_id=chosen_player_id,
            chosen_card_id=chosen_card_id,
        )
        # Give reaction-card holders their window BEFORE committing. If one
        # opens, the play is suspended (PendingPlay) and resolves via
        # _commit_pending when the window closes.
        if await self._maybe_open_reaction_window(player_id, card_id, card, plan, ctx):
            return
        await self._finish_play(player_id, card_id, card, plan, ctx, correlation_id=correlation_id)

    async def _finish_play(
        self,
        player_id: str,
        card_id: str,
        card,
        plan: ResolutionPlan,
        ctx: HookContext,
        *,
        correlation_id: str,
        negated: bool = False,
        steal_to: str | None = None,
        redirect_to: str | None = None,
    ) -> None:
        """Commit a resolved play: zone move + effects + logs + turn accounting.

        The tail of every play, direct or after a reaction window:
        - ``negated``: the plan never executes; the card goes hand → discard.
        - ``steal_to``: the plan never executes; the card goes to that player's hand.
        - ``redirect_to``: the plan executes with that player as the effect actor
          (the zone move still empties the original actor's hand).
        A countered/stolen play still consumes the actor's play allowance. A plan
        pausing on an interaction barrier routes to _pause_resolution, which owns
        the rest of the play (PlanPaused must never fall into the generic
        fallback).
        """
        title = self._card_title(card)
        game_ending = False
        before = {p.id: p.score for p in self.state.players}
        deck_count_before = len(self.state.deck)
        if negated or steal_to is not None:
            if steal_to is not None:
                self.state = self.state.move_card(
                    card_id, "hand", "hand", from_player_id=player_id, to_player_id=steal_to
                )
                self._set_card_mechanical_status(card_id, "countered", correlation_id, "Stolen by a reaction.")
            else:
                self.state = self.state.move_card(
                    card_id, "hand", "discard", from_player_id=player_id, to_player_id=player_id
                )
                self._set_card_mechanical_status(card_id, "countered", correlation_id, "Countered by a reaction.")
        else:
            exec_ctx = ctx if redirect_to is None else replace(ctx, actor_id=redirect_to)
            try:
                self.state = await self._execute_plan(self.state, plan, exec_ctx, card, zone_owner=player_id)
            except PlanPaused as paused:
                try:
                    await self._pause_resolution(
                        paused,
                        plan=plan,
                        ctx=exec_ctx,
                        card=card if isinstance(card, dict) else card.model_dump(),
                        correlation_id=correlation_id,
                        before_scores=before,
                        deck_count_before=deck_count_before,
                        zone_owner=player_id,
                    )
                except Exception as exc:
                    reason = self._public_mechanical_reason(
                        exc, fallback="The interaction could not be started safely."
                    )
                    logger.warning(
                        "interaction setup failed correlation_id=%s card_id=%s reason=%s",
                        correlation_id,
                        card_id,
                        reason,
                    )
                    self._set_card_mechanical_status(card_id, "fallback", correlation_id, reason)
                    destination = self._play_destination(card)
                    self.state = self.state.move_card(
                        card_id, "hand", destination, from_player_id=player_id, to_player_id=player_id
                    )
                    await self._log_and_broadcast(f"[interaction error] {title}: {exc}")
                    fallback = EffectProgram(
                        ops=[CustomNoteOp(note=f"Played {title or 'Card'} (no mechanical effect)")]
                    )
                    self.state = apply_effect(self.state, fallback, ctx, bus=self._hook_bus())
                    await self._log_and_broadcast(self._describe_play(player_id, card, before))
                else:
                    return
            except Exception as exc:
                reason = self._public_mechanical_reason(
                    exc,
                    fallback="The interpreted effect could not be applied safely.",
                )
                logger.warning(
                    "resolution plan failed correlation_id=%s card_id=%s reason=%s",
                    correlation_id,
                    card_id,
                    reason,
                )
                self._set_card_mechanical_status(card_id, "fallback", correlation_id, reason)
                destination = self._play_destination(card)
                self.state = self.state.move_card(
                    card_id,
                    "hand",
                    destination,
                    from_player_id=player_id,
                    to_player_id=player_id,
                )
                await self._log_and_broadcast(f"[snippet error] {title}: {exc}")
                fallback = EffectProgram(ops=[CustomNoteOp(note=f"Played {title or 'Card'} (no mechanical effect)")])
                self.state = apply_effect(self.state, fallback, ctx, bus=self._hook_bus())
                await self._log_and_broadcast(self._describe_play(player_id, card, before))
            else:
                current = self.state.cards.get(card_id)
                if not isinstance(current, dict) or current.get("mechanical_status") != "fallback":
                    self._set_card_mechanical_status(card_id, "applied", correlation_id)
                await self._log_and_broadcast(self._describe_play(player_id, card, before))
                await self._emit_hooks(GameEvent.ON_PLAY, player_id, card_id=card_id)
                game_ending = self._end_now() or win_condition_met(self.state)

        # The play's target for history purposes: whoever the player explicitly
        # chose (prompt_choice) or, for a countered play stolen to a reactor,
        # that reactor. Cards with no chooser (self-only, or "everyone" via
        # ops-level target="all") record no target — we don't fabricate one
        # from ops we haven't inspected (see docstring on history semantics).
        history_target = [ctx.chosen_player_id] if ctx.chosen_player_id else ([steal_to] if steal_to else [])
        await self._after_play_effects(
            player_id,
            card_id,
            game_ending=game_ending,
            deck_count_before=deck_count_before,
            target_player_ids=history_target,
        )

    async def _after_play_effects(
        self,
        player_id: str,
        card_id: str,
        *,
        game_ending: bool,
        deck_count_before: int,
        target_player_ids: list[str] | None = None,
        extra_history_event: dict | None = None,
    ) -> None:
        """The single post-play accounting tail, shared by direct plays
        (_finish_play) and resumed interaction plays (_complete_interaction_play):
        history, deck-exhaustion latch, play allowance, broadcast, end/advance.
        Runs exactly once per original play regardless of outcome.

        ``target_player_ids`` records who (beyond the actor) this play was
        aimed at, when known — e.g. a card played to a chosen player, or
        an interaction's resolved audience. Defaults to empty (no known
        target) rather than the actor, since actor_id already covers that.
        """
        self.state = append_history_event(
            self.state,
            "play",
            actor_id=player_id,
            target_player_ids=target_player_ids if target_player_ids is not None else [],
            card_id=card_id,
            source="resolved",
        )
        if extra_history_event is not None:
            self.state = append_history_event(self.state, **extra_history_event)
        if self.state.rules.end_condition.type == "deck_empty" and deck_count_before > 0 and not self.state.deck:
            self._deck_exhausted = True

        self._plays_this_turn += 1
        await self._broadcast_state()
        if game_ending:
            # end_game / a live win condition ends the game NOW, deck or no deck —
            # unlike deck exhaustion, which lets the drawer finish their turn.
            await self._end_game()
        elif self._plays_this_turn < self.state.rules.play:
            # rules.play > 1: the turn continues until the play allowance is
            # spent (or the player passes).
            await self._broadcast_state()
        else:
            await self._advance_turn()

    # ── generic interaction barriers ──
    @staticmethod
    def _resolve_interaction_ref(results: dict, result_key: str, path: list[str | int]):
        try:
            value = results[result_key]
            for part in path:
                value = value[part]
            return value
        except (KeyError, IndexError, TypeError) as exc:
            raise PlanExecutionError(f"interaction reference {result_key!r} has invalid path {path!r}") from exc

    def _resolve_interaction_audience(self, audience: str, actor_id: str) -> list[str]:
        player_ids = [player.id for player in self.state.players]
        if audience == "active":
            return [actor_id]
        if audience == "all":
            return player_ids
        if audience == "all_others":
            return [player_id for player_id in player_ids if player_id != actor_id]
        if audience.startswith("player:"):
            player_id = audience.removeprefix("player:")
            return [player_id] if player_id in player_ids else []
        return []

    def _materialize_interaction(
        self,
        step: InteractionStep,
        results: dict[str, object],
    ) -> tuple[InteractionDescriptor, dict[str, object]]:
        refs = {
            name: self._resolve_interaction_ref(results, ref.result_key, ref.path)
            for name, ref in step.input_refs.items()
        }
        request = step.request
        if isinstance(request, ChoiceInteraction) and "options" in refs:
            source = refs["options"]
            if not isinstance(source, dict):
                raise PlanExecutionError("choice options reference must resolve to an object")
            options = [
                InteractionOption(
                    id=str(player_id),
                    label=self._name(str(player_id)),
                    payload=value,
                )
                for player_id, value in source.items()
            ]
            request = ChoiceInteraction.model_validate(
                {
                    **request.model_dump(mode="python"),
                    "options": [option.model_dump(mode="python") for option in options],
                    "max_selections": min(request.max_selections, len(options)),
                }
            )
        if isinstance(request, CardPickInteraction) and "card_ids" in refs:
            if not isinstance(refs["card_ids"], list) or not refs["card_ids"]:
                raise PlanExecutionError("card_ids reference must resolve to a non-empty list")
            request = CardPickInteraction.model_validate(
                {**request.model_dump(mode="python"), "card_ids": list(refs["card_ids"])}
            )
        return request, refs

    async def _pause_resolution(
        self,
        paused: PlanPaused,
        *,
        plan: ResolutionPlan,
        ctx: HookContext,
        card: dict,
        correlation_id: str,
        before_scores: dict[str, int],
        deck_count_before: int,
        zone_owner: str | None = None,
    ) -> None:
        request, refs = self._materialize_interaction(paused.step, ctx.interactions)
        audience = self._resolve_interaction_audience(request.audience, ctx.actor_id)
        if not audience:
            raise PlanExecutionError("interaction has no eligible audience")
        interaction_id = uuid.uuid4().hex
        deadline = datetime.now(UTC) + timedelta(seconds=request.timeout_seconds)
        self._pending_resolution = PendingResolution(
            interaction_id=interaction_id,
            card_id=ctx.card_id or "",
            actor_id=ctx.actor_id,
            zone_owner=zone_owner or ctx.actor_id,
            card=card,
            plan=plan,
            cursor=paused.cursor + 1,
            working_state=paused.working_state,
            request=request,
            result_key=paused.step.result_key,
            resolved_audience=audience,
            deadline_at=deadline,
            interactions=ctx.interactions,
            interaction_refs=refs,
            correlation_id=correlation_id,
            chosen_player_id=ctx.chosen_player_id,
            chosen_card_id=ctx.chosen_card_id,
            before_scores=before_scores,
            deck_count_before=deck_count_before,
        )
        self._set_card_mechanical_status(ctx.card_id or "", "pending", correlation_id)
        self._schedule_interaction_timeout()
        for player_id in audience:
            await self._send_interaction_request(player_id)
        await self._broadcast_interaction_progress()
        await self._broadcast_state()

    def _interaction_progress(self, player_id: str | None = None) -> InteractionProgress:
        pending = self._pending_resolution
        if pending is None:
            return InteractionProgress(expected_count=0, received_count=0, complete=True)
        return InteractionProgress(
            expected_count=len(pending.resolved_audience),
            received_count=len(pending.responses),
            submitted=player_id in pending.responses if player_id else False,
            complete=len(pending.responses) >= len(pending.resolved_audience),
        )

    async def _send_interaction_request(self, player_id: str) -> None:
        pending = self._pending_resolution
        if pending is None or player_id not in pending.resolved_audience:
            return
        await self.connections.send(
            player_id,
            {
                "type": "interaction_request",
                "schema_version": 1,
                "interaction_id": pending.interaction_id,
                "descriptor": self._descriptor_for(pending.request, player_id),
                "deadline_at": pending.deadline_at.isoformat(),
                "progress": self._interaction_progress(player_id).model_dump(),
            },
        )

    def _descriptor_for(self, request: InteractionDescriptor, player_id: str) -> dict:
        """Serialise the descriptor for one recipient.

        A ``from_hand`` card_pick is personalised: each player is shown THEIR OWN
        hand as the selectable ``card_ids`` (see :func:`_validate_interaction_response`,
        which mirrors this by validating against the responder's hand)."""
        descriptor = request.model_dump(mode="json")
        if isinstance(request, CardPickInteraction) and request.from_hand:
            descriptor["card_ids"] = list(self._from_hand_options(player_id))
        return descriptor

    def _from_hand_options(self, player_id: str) -> list[str]:
        """The hand a from_hand card_pick offers ``player_id``.

        Reads the paused resolution's working_state when one is live (the played
        card has already left the actor's hand there), so the actor is never
        offered the card they are mid-play — falling back to committed state."""
        source = self.state
        if self._pending_resolution is not None:
            source = self._pending_resolution.working_state
        try:
            return list(source.get_player(player_id).hand)
        except KeyError:
            return []

    async def _broadcast_interaction_progress(self) -> None:
        pending = self._pending_resolution
        if pending is None:
            return
        for player_id in self.connections.connected_players:
            await self.connections.send(
                player_id,
                {
                    "type": "interaction_progress",
                    "schema_version": 1,
                    "interaction_id": pending.interaction_id,
                    "deadline_at": pending.deadline_at.isoformat(),
                    "progress": self._interaction_progress(player_id).model_dump(),
                },
            )

    async def replay_pending_interaction(self, player_id: str) -> None:
        if self._pending_resolution is None:
            return
        self._schedule_interaction_timeout()
        await self._send_interaction_request(player_id)

    def ensure_pending_timeout(self) -> None:
        """Resume persisted interaction deadlines without waiting for reconnect."""
        if self._pending_resolution is not None:
            self._schedule_interaction_timeout()

    def _schedule_interaction_timeout(self) -> None:
        pending = self._pending_resolution
        if pending is None:
            return
        if (
            self._interaction_timer is not None
            and self._interaction_timer is not asyncio.current_task()
            and not self._interaction_timer.done()
        ):
            self._interaction_timer.cancel()
        delay = max(0.0, (pending.deadline_at - datetime.now(UTC)).total_seconds())
        self._interaction_timer = asyncio.create_task(self._interaction_timeout(pending.interaction_id, delay))

    async def _interaction_timeout(self, interaction_id: str, delay: float) -> None:
        await asyncio.sleep(delay)
        async with self._lock:
            pending = self._pending_resolution
            if pending is None or pending.interaction_id != interaction_id:
                return
            await self._resume_pending_resolution(timed_out=True)
            self._notify_change()

    def _validate_interaction_response(
        self,
        request: InteractionDescriptor,
        payload: InteractionResponsePayload,
        player_id: str | None = None,
    ) -> object:
        if payload.kind != request.kind:
            raise ValueError("response kind does not match request")
        if isinstance(request, NumberInteraction) and isinstance(payload, NumberResponse):
            if not request.minimum <= payload.value <= request.maximum:
                raise ValueError("number is outside the allowed range")
            if request.integer and not payload.value.is_integer():
                raise ValueError("an integer is required")
            return int(payload.value) if request.integer else payload.value
        if isinstance(request, TextInteraction) and isinstance(payload, TextResponse):
            if len(payload.value) > request.max_length:
                raise ValueError("text is too long")
            return payload.value
        if isinstance(request, ChoiceInteraction) and isinstance(payload, ChoiceResponse):
            option_ids = payload.option_ids
            if not request.min_selections <= len(option_ids) <= request.max_selections:
                raise ValueError("wrong number of choices")
            allowed = {option.id for option in request.options}
            if not set(option_ids) <= allowed:
                raise ValueError("unknown choice")
            return option_ids
        if isinstance(request, CardPickInteraction) and isinstance(payload, CardPickResponse):
            # from_hand picks are validated against the responder's own hand (the
            # per-player option set _send_interaction_request presented), not the
            # static card_ids (which is empty for a from_hand pick).
            if request.from_hand:
                selectable = set(self._from_hand_options(player_id)) if player_id is not None else set()
            else:
                selectable = set(request.card_ids)
            if payload.card_id not in selectable:
                raise ValueError("card is not selectable")
            return payload.card_id
        if isinstance(request, ConfirmInteraction) and isinstance(payload, ConfirmResponse):
            return payload.confirmed
        if isinstance(request, DrawingInteraction) and isinstance(payload, DrawingResponse):
            if len(payload.strokes) > request.max_strokes or any(
                len(stroke.points) > request.max_points_per_stroke for stroke in payload.strokes
            ):
                raise ValueError("drawing exceeds request limits")
            return [stroke.model_dump() for stroke in payload.strokes]
        raise ValueError("invalid interaction response")

    async def _handle_interaction_response(self, player_id: str, msg) -> None:
        pending = self._pending_resolution
        if pending is None or msg.interaction_id != pending.interaction_id:
            await self.connections.send(player_id, {"type": "error", "message": "Interaction is no longer active"})
            return
        if player_id not in pending.resolved_audience:
            await self.connections.send(player_id, {"type": "error", "message": "You are not part of this interaction"})
            return
        if player_id in pending.responses:
            await self.connections.send(player_id, {"type": "error", "message": "Response already submitted"})
            return
        if datetime.now(UTC) >= pending.deadline_at:
            await self._resume_pending_resolution(timed_out=True)
            await self.connections.send(player_id, {"type": "error", "message": "Interaction deadline passed"})
            return
        try:
            self._validate_interaction_response(pending.request, msg.payload, player_id)
        except ValueError as exc:
            await self.connections.send(player_id, {"type": "error", "message": str(exc)})
            return
        pending.responses[player_id] = msg.payload
        await self._send_interaction_request(player_id)
        await self._broadcast_interaction_progress()
        if len(pending.responses) >= len(pending.resolved_audience):
            await self._resume_pending_resolution(timed_out=False)

    @staticmethod
    def _default_interaction_value(request: InteractionDescriptor) -> object:
        if isinstance(request, NumberInteraction):
            bounded = max(request.minimum, min(0, request.maximum))
            return (
                int(bounded)
                if request.integer and bounded.is_integer()
                else (math.ceil(request.minimum) if request.integer else bounded)
            )
        if isinstance(request, TextInteraction):
            return ""
        if isinstance(request, ChoiceInteraction):
            return []
        if isinstance(request, CardPickInteraction):
            return None
        if isinstance(request, ConfirmInteraction):
            return False
        return []

    async def _resume_pending_resolution(self, *, timed_out: bool) -> None:
        pending = self._pending_resolution
        if pending is None:
            return
        if (
            self._interaction_timer is not None
            and self._interaction_timer is not asyncio.current_task()
            and not self._interaction_timer.done()
        ):
            self._interaction_timer.cancel()
        if timed_out and not pending.responses:
            await self._fail_pending_resolution("No one responded before the interaction timed out.")
            return
        values: dict[str, object] = {}
        for player_id in pending.resolved_audience:
            payload = pending.responses.get(player_id)
            values[player_id] = (
                self._validate_interaction_response(pending.request, payload, player_id)
                if payload is not None
                else self._default_interaction_value(pending.request)
            )
        interactions = {**pending.interactions, pending.result_key: values}
        self._pending_resolution = None
        ctx = HookContext(
            event=GameEvent.ON_PLAY,
            actor_id=pending.actor_id,
            card_id=pending.card_id,
            chosen_player_id=pending.chosen_player_id,
            chosen_card_id=pending.chosen_card_id,
            interactions=interactions,
            interaction_refs=pending.interaction_refs,
        )
        try:
            completed = await self._execute_plan(
                self.state,
                pending.plan,
                ctx,
                pending.card,
                start_cursor=pending.cursor,
                working_state=pending.working_state,
            )
        except PlanPaused as paused:
            try:
                await self._pause_resolution(
                    paused,
                    plan=pending.plan,
                    ctx=ctx,
                    card=pending.card,
                    correlation_id=pending.correlation_id,
                    before_scores=pending.before_scores,
                    deck_count_before=pending.deck_count_before,
                )
            except Exception as exc:
                await self._fail_pending_resolution(
                    self._public_mechanical_reason(exc, fallback="The next interaction could not be started safely."),
                    pending=pending,
                )
            return
        except Exception as exc:
            await self._fail_pending_resolution(
                self._public_mechanical_reason(exc, fallback="The interaction effect could not be applied safely."),
                pending=pending,
            )
            return
        await self._commit_pending_resolution(pending, completed)

    async def _fail_pending_resolution(self, reason: str, *, pending: PendingResolution | None = None) -> None:
        pending = pending or self._pending_resolution
        if pending is None:
            return
        self._pending_resolution = None
        self._set_card_mechanical_status(pending.card_id, "fallback", pending.correlation_id, reason)
        destination = self._play_destination(pending.card)
        owner = pending.zone_owner or pending.actor_id
        self.state = self.state.move_card(
            pending.card_id,
            "hand",
            destination,
            from_player_id=owner,
            to_player_id=owner,
        )
        ctx = HookContext(event=GameEvent.ON_PLAY, actor_id=pending.actor_id, card_id=pending.card_id)
        self.state = apply_effect(
            self.state,
            EffectProgram(ops=[CustomNoteOp(note=f"Played {self._card_title(pending.card)} (no mechanical effect)")]),
            ctx,
            bus=self._hook_bus(),
        )
        await self._log_and_broadcast(f"[interaction] {reason}")
        await self._complete_interaction_play(pending, game_ending=False)

    async def _commit_pending_resolution(self, pending: PendingResolution, completed: GameState) -> None:
        self.state = completed
        self._set_card_mechanical_status(pending.card_id, "applied", pending.correlation_id)
        await self._log_and_broadcast(self._describe_play(pending.actor_id, pending.card, pending.before_scores))
        await self._emit_hooks(GameEvent.ON_PLAY, pending.actor_id, card_id=pending.card_id)
        await self._complete_interaction_play(
            pending,
            game_ending=self._end_now() or win_condition_met(self.state),
        )

    async def _complete_interaction_play(self, pending: PendingResolution, *, game_ending: bool) -> None:
        # The interaction's resolved audience (minus the actor) doubles as the
        # "play" event's target for history purposes — it's who the play's
        # interaction actually addressed.
        target_player_ids = [pid for pid in pending.resolved_audience if pid != pending.actor_id]
        await self._after_play_effects(
            pending.actor_id,
            pending.card_id,
            game_ending=game_ending,
            deck_count_before=pending.deck_count_before,
            target_player_ids=target_player_ids,
            extra_history_event={
                "kind": "interaction",
                "actor_id": pending.actor_id,
                "target_player_ids": pending.resolved_audience,
                "card_id": pending.card_id,
                "source": pending.result_key,
            },
        )

    # ── reaction window ──
    async def _maybe_open_reaction_window(
        self, player_id: str, card_id: str, card, plan: ResolutionPlan, ctx: HookContext
    ) -> bool:
        """Open a reaction window for this play if anyone can react.

        Eligibility (computed once, at open): connected players other than the
        actor holding at least one reaction card. Skipped entirely when nobody
        is eligible or the pending card is uncounterable — no 15s stall on
        ordinary plays. Returns True when the play is now suspended.
        """
        if self._is_uncounterable(card):
            return False
        connected = set(self.connections.connected_players)
        eligible = {
            p.id
            for p in self.state.players
            if p.id != player_id
            and p.id in connected
            and any(self._is_reaction_card(self.state.cards.get(cid, {})) for cid in p.hand)
        }
        if not eligible:
            return False
        window_id = uuid.uuid4().hex
        pending = PendingPlay(
            window_id=window_id,
            actor_id=player_id,
            card_id=card_id,
            card=card if isinstance(card, dict) else card.model_dump(),
            plan=plan,
            chosen_player_id=ctx.chosen_player_id,
            chosen_card_id=ctx.chosen_card_id,
            eligible_ids=eligible,
            deadline=time.time() + REACTION_WINDOW_SECONDS,
        )
        pending.timer = asyncio.create_task(self._reaction_timeout(window_id, REACTION_WINDOW_SECONDS))
        self._pending = pending
        await self.connections.broadcast(
            {
                "type": "reaction_window",
                "window_id": window_id,
                "card_id": card_id,
                "actor_id": player_id,
                "deadline_epoch_ms": int(pending.deadline * 1000),
            }
        )
        await self._log_and_broadcast(f"{self._name(player_id)} plays {self._card_title(card)}… waiting for reactions")
        await self._broadcast_state()
        return True

    async def _reaction_timeout(self, window_id: str, delay: float) -> None:
        """Auto-resolve the pending play when the window times out.

        Takes the same lock as handle_action, so 'timeout races a reaction'
        reduces to whoever wins the lock; the loser sees a cleared/mismatched
        window_id and no-ops.
        """
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        async with self._lock:
            pending = self._pending
            if pending is None or pending.window_id != window_id:
                return
            await self._commit_pending("resolved")
            self._notify_change()

    async def _commit_pending(
        self,
        outcome: str,
        *,
        reactor_id: str | None = None,
        reaction_card_id: str | None = None,
    ) -> None:
        """Close the reaction window and commit the suspended play accordingly.

        Callers hold the room lock. Clears ``_pending`` FIRST so re-entrant
        paths (stale timer, late reactions) see a closed window.
        """
        pending = self._pending
        if pending is None:
            return
        self._pending = None
        if pending.timer is not None and not pending.timer.done():
            pending.timer.cancel()
        await self.connections.broadcast(
            {
                "type": "reaction_result",
                "window_id": pending.window_id,
                "outcome": outcome,
                "reactor_id": reactor_id,
                "reaction_card_id": reaction_card_id,
            }
        )
        ctx = HookContext(
            event=GameEvent.ON_PLAY,
            actor_id=pending.actor_id,
            card_id=pending.card_id,
            chosen_player_id=pending.chosen_player_id,
            chosen_card_id=pending.chosen_card_id,
        )
        correlation_id = str(uuid.uuid4())
        title = self._card_title(pending.card)
        if outcome == "countered":
            await self._log_and_broadcast(f"{self._name(pending.actor_id)}'s {title} was countered!")
            await self._finish_play(
                pending.actor_id,
                pending.card_id,
                pending.card,
                pending.plan,
                ctx,
                correlation_id=correlation_id,
                negated=True,
            )
        elif outcome == "stolen":
            await self._log_and_broadcast(
                f"{self._name(reactor_id)} steals {title} from {self._name(pending.actor_id)}!"
            )
            await self._finish_play(
                pending.actor_id,
                pending.card_id,
                pending.card,
                pending.plan,
                ctx,
                correlation_id=correlation_id,
                steal_to=reactor_id,
            )
        elif outcome == "redirected":
            await self._log_and_broadcast(f"{title} is redirected — it resolves for {self._name(reactor_id)}!")
            await self._finish_play(
                pending.actor_id,
                pending.card_id,
                pending.card,
                pending.plan,
                ctx,
                correlation_id=correlation_id,
                redirect_to=reactor_id,
            )
        else:
            await self._finish_play(
                pending.actor_id,
                pending.card_id,
                pending.card,
                pending.plan,
                ctx,
                correlation_id=correlation_id,
            )

    async def _handle_reaction_play(self, player_id: str, msg) -> None:
        """A non-active player plays a reaction card into the open window."""
        pending = self._pending

        async def err(message: str) -> None:
            await self.connections.send(player_id, {"type": "error", "message": message})

        if pending is None:
            await err("The reaction window has closed")
            return
        if player_id == pending.actor_id:
            await err("You cannot react to your own play")
            return
        if player_id not in pending.eligible_ids:
            await err("You have no reaction to play")
            return
        if player_id in pending.passed_ids:
            await err("You already passed on this play")
            return
        if pending.claimed_by not in (None, player_id):
            await err("Another player is already reacting")
            return
        card_id = msg.card_id
        if card_id not in self.state.get_player(player_id).hand:
            await err("That card is not in your hand")
            return
        card = self.state.cards.get(card_id)
        if card is None or self._is_blank(card):
            await err("Blank cards cannot be played as reactions")
            return
        if not self._is_reaction_card(card):
            await err(f"{self._card_title(card)} is not a reaction card")
            return

        # Claim the window and restart the timer: the resolve below may need an
        # LLM round-trip and/or a prompt_choice follow-up, and an abandoned
        # follow-up must never wedge the room.
        pending.claimed_by = player_id
        if pending.timer is not None and not pending.timer.done():
            pending.timer.cancel()
        pending.deadline = time.time() + REACTION_WINDOW_SECONDS
        pending.timer = asyncio.create_task(self._reaction_timeout(pending.window_id, REACTION_WINDOW_SECONDS))

        correlation_id = str(uuid.uuid4())
        plan = await self._resolve_plan(card_id, card, actor_id=player_id, correlation_id=correlation_id)
        if any(isinstance(step, InteractionStep) for step in plan.steps):
            # v1 limitation: a reaction resolving inside the window cannot open
            # an interaction barrier of its own.
            pending.claimed_by = None
            await err(f"{self._card_title(card)} needs player input — reactions cannot open interactions")
            return
        chosen_player_id = getattr(msg, "chosen_player_id", None)
        chosen_card_id = getattr(msg, "chosen_card_id", None)
        ops = plan.operations()
        needs_player_choice = any(
            getattr(op, field_name, None) in ("chooser", "target_player")
            for op in ops
            for field_name in ("target", "from_target", "to_target")
        )
        if needs_player_choice and chosen_player_id is None:
            # Same suspend/resume as a normal play: the follow-up play message
            # re-enters here carrying as_reaction + the choice.
            await self.connections.send(
                player_id,
                {
                    "type": "prompt_choice",
                    "card_id": card_id,
                    "prompt": f"Choose a target player for {self._card_title(card)}",
                    "choices": [{"player_id": p.id, "name": p.name} for p in self.state.players],
                },
            )
            return
        if chosen_player_id is not None and chosen_player_id not in {p.id for p in self.state.players}:
            await err(f"Invalid target player: {chosen_player_id}")
            return

        try:
            mode = await self._execute_reaction(player_id, card_id, card, plan, chosen_player_id, chosen_card_id)
        except Exception as exc:
            logger.warning("reaction %s failed: %s", card_id, exc)
            pending.claimed_by = None  # unclaim; they may retry or pass
            await err(f"Reaction failed: {exc}")
            return
        self._set_card_mechanical_status(card_id, "applied", correlation_id)
        outcome = {"negate": "countered", "steal_hand": "stolen", "redirect": "redirected"}.get(mode or "", "resolved")
        await self._commit_pending(outcome, reactor_id=player_id, reaction_card_id=card_id)

    async def _handle_pass_reaction(self, player_id: str, msg) -> None:
        """An eligible player declines to react; all-passed closes the window early."""
        pending = self._pending
        if pending is None:
            return  # window already closed — a late pass is harmless
        window_id = getattr(msg, "window_id", None)
        if window_id is not None and window_id != pending.window_id:
            return
        if player_id not in pending.eligible_ids or player_id in pending.passed_ids:
            return
        pending.passed_ids.add(player_id)
        if pending.passed_ids >= pending.eligible_ids and pending.claimed_by is None:
            await self._commit_pending("resolved")
        else:
            await self._broadcast_state()

    async def _execute_reaction(
        self,
        reactor_id: str,
        reaction_card_id: str,
        card,
        plan: ResolutionPlan,
        chosen_player_id: str | None,
        chosen_card_id: str | None,
    ) -> str | None:
        """Apply a reaction card's own effects and extract its counter mode.

        counter_play ops are control flow, not state changes: they are
        partitioned out of both OpsStep ops and snippet diffs, and the first
        one's mode is returned (None = damp squib — the original play still
        resolves). Everything else applies through the normal reducer path, so
        "counter and gain 2" works.
        """
        from config import get_settings
        from engine.sandbox.revalidate import apply_snippet_diff, extract_counter
        from engine.sandbox.runner import execute_snippet
        from models.effects import CounterPlayOp

        pending = self._pending
        ctx = HookContext(
            event=GameEvent.ON_REACTION,
            actor_id=reactor_id,
            card_id=reaction_card_id,
            chosen_player_id=chosen_player_id,
            chosen_card_id=chosen_card_id,
            extra={
                "pending_card_id": pending.card_id,
                "pending_actor_id": pending.actor_id,
                "pending_card_title": self._card_title(pending.card),
                # Op dumps only — a reaction can inspect what the pending play
                # does, never its snippet source.
                "pending_ops": [op.model_dump() for op in pending.plan.operations()],
            },
        )
        before = {p.id: p.score for p in self.state.players}
        destination = self._play_destination(card)
        working = self.state.move_card(
            reaction_card_id, "hand", destination, from_player_id=reactor_id, to_player_id=reactor_id
        )
        rng = random.Random()
        ctx_dict = {
            "actor_id": reactor_id,
            "event": str(GameEvent.ON_REACTION),
            "card_id": reaction_card_id,
            "amount": None,
            "chosen_player_id": chosen_player_id,
            "chosen_card_id": chosen_card_id,
            **ctx.extra,
        }
        mode: str | None = None
        for step in plan.steps:
            bus = EventBus(build_registry(working), max_hooks=MAX_HOOKS_PER_EVENT)
            if isinstance(step, OpsStep):
                side_ops = []
                for op in step.ops:
                    if isinstance(op, CounterPlayOp):
                        mode = mode or op.mode
                    else:
                        side_ops.append(op)
                if side_ops:
                    working = apply_effect(working, EffectProgram(ops=side_ops), ctx, bus=bus, rng=rng)
                continue
            if not get_settings().snippet_execution_enabled:
                raise PlanExecutionError("snippet execution is disabled")
            state_dict = json.loads(working.model_dump_json())
            raw_ops = await asyncio.to_thread(execute_snippet, step.code, state_dict, ctx_dict)
            step_mode, side_raw = extract_counter(raw_ops)
            mode = mode or step_mode
            working = apply_snippet_diff(working, side_raw, ctx, origin="reaction", bus=bus, rng=rng)
        self.state = working
        deltas = {p.id: p.score - before.get(p.id, p.score) for p in self.state.players}
        line = f"{self._name(reactor_id)} reacts with {self._card_title(card)}"
        formatted = self._format_score_deltas(deltas)
        if formatted:
            line += f" ({formatted})"
        await self._log_and_broadcast(line)
        await self._emit_hooks(GameEvent.ON_REACTION, reactor_id, card_id=reaction_card_id)
        await self._broadcast_state()
        return mode

    async def _handle_create_card(self, player_id: str, msg) -> None:
        """Author a new card during SETUP — the only create_card path
        (``_dispatch`` rejects the message in every other phase; mid-game
        authoring happens only by playing a blank, see ``_handle_play``).

        Authoring never calls the LLM: authored cards are interpreted
        deterministically (via ``compile_card``) or best-effort at play time, so
        setup authoring stays fast and never depends on a live service. The card
        is simply registered with its ``creator_id`` (which drives
        ``setup_progress`` and the start gate) and broadcast.

        Authoring is capped at ``CARDS_TO_AUTHOR`` per player: the start gate
        only enforces a LOWER bound, so without this cap a player could author
        unlimited cards before the game starts.

        Once this card completes the LAST player's authoring quota, the game
        AUTO-STARTS (setup→playing) — no manual "start" is required.
        """
        # Upper bound: reject (targeted, not broadcast) once the player has
        # authored the required number of cards, BEFORE any card is created.
        if self._authored_count(player_id) >= CARDS_TO_AUTHOR:
            await self.connections.send(
                player_id,
                {"type": "error", "message": f"You've already authored the maximum of {CARDS_TO_AUTHOR} cards."},
            )
            return

        card_id = str(uuid.uuid4())
        art = msg.art
        if art and not self._store_card_art(card_id, art):
            art = None
            await self.connections.send(
                player_id,
                {"type": "error", "message": "This room's art storage is full — card created without art"},
            )
        new_cards = {
            **self.state.cards,
            card_id: {
                "id": card_id,
                "title": msg.title,
                "description": msg.description,
                "creator_id": player_id,
                "origin": "authored",
                "has_art": bool(art),
                "mechanical_status": "pending",
                "mechanical_reason": None,
                "correlation_id": str(uuid.uuid4()),
            },
        }
        self.state = self.state.model_copy(update={"cards": new_cards})

        # Auto-start: once every non-spectator has authored the required
        # number of cards, transition straight to playing — no manual
        # "start" needed. Guard the degenerate zero-players case so we never
        # auto-start an empty table. ``_start_playing`` broadcasts the new
        # (playing) state itself, so we don't also broadcast the pre-start
        # setup state on this path.
        real_players = self.state.turn_players()
        everyone_done = bool(real_players) and all(self._authored_count(p.id) >= CARDS_TO_AUTHOR for p in real_players)
        if everyone_done:
            await self._start_playing()
        else:
            await self._broadcast_state()

    async def _handle_preview_card(self, player_id: str, msg) -> None:
        """Interpret and execute against a clone, returning diagnostics only.

        Setup-only, like create_card (gated in ``_dispatch``): an opt-in check
        of a draft card before submitting it. Unlike setup create_card this DOES
        call the LLM — the whole point is a real interpretation dry-run — but it
        never mutates the room, so a dead service just fails the preview.
        """
        from agent.contract import InterpretResult
        from agent.runtime import run_agent
        from agent.tools.dry_run_effect import dry_run_resolution_plan

        correlation_id = str(uuid.uuid4())
        player_ids = {player.id for player in self.state.players}
        actor_id = player_id if player_id in player_ids else self.state.active_player().id
        preview_id = f"preview:{correlation_id}"
        preview_state = self.state.model_copy(deep=True)
        preview_state = preview_state.model_copy(
            update={
                "cards": {
                    **preview_state.cards,
                    preview_id: {
                        "id": preview_id,
                        "title": msg.title,
                        "description": msg.description,
                        "creator_id": actor_id,
                        "origin": "authored",
                    },
                },
                "players": [
                    player.model_copy(update={"hand": [*player.hand, preview_id]}) if player.id == actor_id else player
                    for player in preview_state.players
                ],
            }
        )
        try:
            result: InterpretResult = await asyncio.to_thread(
                run_agent,
                msg.title,
                msg.description,
                preview_state,
                actor_id,
                creator_id=actor_id,
                card_id=preview_id,
                allow_persistent_tools=False,
            )
            plan = result.to_plan()
            if result.verdict != "ok" or not plan.steps:
                status = "fallback"
                reason = "The arbiter could not produce an executable effect."
                report = None
            else:
                choice_player = next((candidate for candidate in sorted(player_ids) if candidate != actor_id), actor_id)
                choice_card = next(
                    (candidate for candidate in preview_state.cards_in_play() if candidate != preview_id),
                    preview_id,
                )
                report = await asyncio.to_thread(
                    dry_run_resolution_plan,
                    preview_state,
                    plan,
                    actor_id,
                    preview_id,
                    chosen_player_id=choice_player,
                    chosen_card_id=choice_card,
                )
                status = "applied" if report["ok"] else "rejected"
                reason = (
                    None
                    if report["ok"]
                    else self._public_mechanical_reason(
                        report.get("error"),
                        fallback="The interpreted effect failed its dry-run.",
                    )
                )
        except Exception as exc:
            logger.exception("preview failed correlation_id=%s", correlation_id)
            result = InterpretResult(verdict="invalid")
            plan = result.to_plan()
            report = None
            status = "rejected"
            reason = self._public_mechanical_reason(exc, fallback="The preview could not be completed.")

        logger.info(
            "card preview correlation_id=%s status=%s actor_id=%s reason=%s",
            correlation_id,
            status,
            actor_id,
            reason,
        )
        await self.connections.send(
            player_id,
            {
                "type": "preview_result",
                "program": plan.model_dump_json() if plan.steps else None,
                "snippet": next(
                    (step.code for step in plan.steps if isinstance(step, SnippetStep)),
                    None,
                ),
                "verdict": result.verdict,
                "mechanical_status": status,
                "mechanical_reason": reason,
                "correlation_id": correlation_id,
            },
        )

    async def start_epilogue(self) -> None:
        """Begin the epilogue phase: gather authored cards and open voting.

        The vote pool is AUTHORED cards only — authored this game or kept from
        a previous game (a RAG re-entry) — never shipped seed cards and never
        un-authored blanks (see :meth:`_is_authored_card`). Voting on played vs.
        unplayed authored cards is intentionally NOT distinguished here: the
        decided policy is "every authored card gets a vote", regardless of
        whether it ever left the deck.
        """
        cards = [c for c in self.state.cards.values() if self._is_authored_card(c)]
        card_dicts = [c if isinstance(c, dict) else c.model_dump() for c in cards]
        # Only real players vote in the epilogue; spectators authored no cards
        # and must not be counted as expected voters (which would stall the tally).
        self._epilogue = EpilogueManager(player_ids=[p.id for p in self.state.turn_players()])
        self.state = self.state.model_copy(update={"phase": "epilogue"})
        await self._epilogue.start(card_dicts, self.connections)
        await self._broadcast_state()

    async def _handle_epilogue_start(self, player_id: str) -> None:
        """Host-only: advance from the results screen into the epilogue vote.

        Mirrors ``_handle_epilogue_finalize``'s host-only convention. Only
        valid from ``phase == "results"`` — the state ``_end_game`` lands on
        so players see final scores + history before voting starts.
        """
        if self.state.phase != "results":
            await self.connections.send(
                player_id, {"type": "error", "message": "Epilogue can only start from the results screen"}
            )
            return
        if not self._is_host(player_id):
            await self.connections.send(player_id, {"type": "error", "message": "Only the host can start the epilogue"})
            return
        await self.start_epilogue()

    async def _handle_epilogue_vote(self, player_id: str, msg) -> None:
        if self._epilogue is None:
            await self.connections.send(player_id, {"type": "error", "message": "No epilogue in progress"})
            return
        self._epilogue.record_vote(player_id, msg.card_id, msg.keep)

    async def _handle_epilogue_done(self, player_id: str) -> None:
        """A player is done voting — cards they never voted on abstain.

        Finalizes once every non-spectator player has signalled done, so a
        player who walks away (or never gets to every card) cannot stall the
        room forever.
        """
        if self._epilogue is None:
            await self.connections.send(player_id, {"type": "error", "message": "No epilogue in progress"})
            return
        if self._epilogue.mark_done(player_id):
            await self._finalize_epilogue()

    async def _handle_epilogue_finalize(self, player_id: str) -> None:
        """Host-only: finalize the epilogue immediately, regardless of who's done."""
        if self._epilogue is None:
            await self.connections.send(player_id, {"type": "error", "message": "No epilogue in progress"})
            return
        if not self._is_host(player_id):
            await self.connections.send(
                player_id, {"type": "error", "message": "Only the host can finalize the epilogue early"}
            )
            return
        await self._finalize_epilogue()

    async def _finalize_epilogue(self) -> None:
        """Tally votes, persist kept cards, and transition to ``ended``.

        Surfaces the outcome as ``state.epilogue_result`` (id+title per card)
        so the final results screen — and a client reconnecting after the
        vote — can render kept/destroyed lists straight from the snapshot.
        """
        result = await self._epilogue.tally_and_persist(card_art=self.card_art)
        epilogue_result = EpilogueResultSummary(
            kept=[
                EpilogueCardOutcome(id=cid, title=self._card_title(self.state.cards.get(cid, {})))
                for cid in result.kept
            ],
            destroyed=[
                EpilogueCardOutcome(id=cid, title=self._card_title(self.state.cards.get(cid, {})))
                for cid in result.destroyed
            ],
        )
        self.state = self.state.model_copy(update={"phase": "ended", "epilogue_result": epilogue_result})
        await self._broadcast_state()
        await self._log_and_broadcast(f"Epilogue complete. Kept: {len(result.kept)} cards.")

    # ── helpers ──
    def snapshot(self) -> dict:
        """JSON-serialisable snapshot of the current GameState.

        Augmented with per-turn transient flags the GameState model doesn't
        carry:
        - ``has_drawn`` — whether the active player's turn-start auto-draw has
          happened (true for the whole turn in practice; kept for client
          compatibility now that drawing is automatic).
        - ``can_pass`` — whether the active player may end their turn without
          playing (only true when they hold NO playable card). The client hides
          the Pass button unless this is true, so pass is never offered while a
          play is possible (e.g. while holding a blank).
        - ``setup_progress`` — {player_id: authored_count} during setup, so the
          client can show "3/5 authored" for everyone and the host knows when
          starting is unblocked.
        """
        snap = self.state.model_dump()
        snap["has_drawn"] = self._has_drawn
        active_id = self.state.active_player().id if self.state.players else None
        snap["can_pass"] = self._can_pass(active_id) if active_id is not None else False
        snap["setup_progress"] = self._setup_progress()
        snap["cards_to_author"] = CARDS_TO_AUTHOR
        pending = self._pending_resolution
        snap["pending_interaction"] = (
            {
                "interaction_id": pending.interaction_id,
                "kind": pending.request.kind,
                "deadline_at": pending.deadline_at.isoformat(),
                "progress": self._interaction_progress().model_dump(),
            }
            if pending is not None
            else None
        )
        # Open reaction window, public info only (reconnect-safe source of
        # truth; the reaction_window push is just the immediacy signal). Each
        # client computes its own eligibility from its hand's canonicals.
        snap["pending_play"] = (
            {
                "window_id": self._pending.window_id,
                "card_id": self._pending.card_id,
                "actor_id": self._pending.actor_id,
                "deadline_epoch_ms": int(self._pending.deadline * 1000),
            }
            if self._pending is not None
            else None
        )
        return snap

    async def _log_and_broadcast(self, log_entry: str) -> None:
        """Append ``log_entry`` to the persistent game log AND broadcast it live.

        The live ``effect_applied`` message drives clients' in-session log, but a
        client that (re)joins or refreshes only gets the state snapshot — so the
        entry must also live in ``state.log`` to survive a reload. Every effect /
        turn log line goes through here to keep the two in sync.
        """
        self.state = self.state.with_log(log_entry)
        await self.connections.broadcast({"type": "effect_applied", "log_entry": log_entry})

    async def _log_agent_comment(self, card_id: str, comment: str) -> None:
        """Persist the interpretation agent's in-character comment to the game log.

        Appends ``AGENT_COMMENT_PREFIX + comment`` to ``state.log`` (and broadcasts
        it live) via :meth:`_log_and_broadcast`, so the arbiter's quip both shows
        up live AND survives a reconnect/refresh (rejoiners only receive the state
        snapshot, whose ``log`` this feeds).

        No-ops on an empty comment (the deterministic compiled path has no comment,
        and we must not spam blank lines) and de-dupes per ``card_id``: a card that
        needs a target is re-resolved after its prompt_choice, so this guards the
        comment to log exactly once per played card.
        """
        if not comment:
            return
        if card_id in self._comment_logged:
            return
        self._comment_logged.add(card_id)
        await self._log_and_broadcast(f"{AGENT_COMMENT_PREFIX}{comment}")

    async def _broadcast_state(self) -> None:
        await self.connections.broadcast_state(self.snapshot())
