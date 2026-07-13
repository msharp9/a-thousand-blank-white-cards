"""board.rooms.room — one game session: GameState + ConnectionManager + turn enforcement.

Room owns an immutable GameState (replaced on each mutation) and serialises all
handle_action calls with an asyncio.Lock so concurrent WebSocket messages cannot
corrupt turn order. Play/pass require the active player's turn; card creation and
preview are allowed off-turn. Play and create_card run the agent interpretation
graph via asyncio.to_thread (with a "brewing" broadcast), applying resulting
effects through the engine.

Turn model (draw → play → end turn): drawing is EXPLICIT. A turn has three
steps: (1) the active player sends ``draw`` to take ``rules.draw`` card(s) — once
per turn, and required before playing/ending while the deck is non-empty; (2)
they ``play`` a card OR (3) ``pass`` / ``end_turn`` to end without playing. Either
ending advances the turn; the next player's draw flag resets and they must draw
themselves. There is no auto-draw.

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
import random
import uuid
from collections.abc import Callable

from engine.apply import apply_effect
from engine.compile import compile_card_plan
from engine.events import EventBus, GameEvent, HookContext
from engine.hooks import build_registry
from engine.loop import advance_turn
from engine.scoring import evaluate_end_condition, evaluate_win_condition, resolve_end_of_game, win_condition_met
from models.card import MAX_ROOM_ART_BYTES
from models.effects import CustomNoteOp, EffectProgram, OpsStep, ResolutionPlan, SnippetStep
from models.game_state import EndCondition, EpilogueCardOutcome, EpilogueResultSummary, GameState, Player, Spectator
from models.ws_messages import CreateCardMsg
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


class PlanExecutionError(Exception):
    pass


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
        # Per-turn bookkeeping for the draw→play→end model. Reset at the start of
        # every turn (see _start_turn). ``_has_drawn`` gates play/pass so a turn
        # follows draw-first; ``_deck_exhausted`` latches once the last card is
        # drawn so the game ends after the drawer finishes their turn.
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
    async def handle_action(self, player_id: str, msg) -> None:
        """Serialised entry point for all client messages."""
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
            "draw",
            "pass",
            "end_turn",
            "play",
            "create_card",
            "preview_card",
        }:
            await self.connections.send(player_id, {"type": "error", "message": "Spectators cannot take game actions"})
            return
        # Phase gate: once the game leaves "playing" (results/epilogue/ended —
        # e.g. an end_game card fired mid-deck), in-game actions must not run;
        # a stray play would re-trigger _end_game and double-apply end scoring.
        if mtype in {"draw", "pass", "end_turn", "play"} and self.state.phase != "playing":
            await self.connections.send(player_id, {"type": "error", "message": "The game is not in play"})
            return
        if mtype in {"create_card", "preview_card"} and self.state.phase not in {"setup", "playing"}:
            await self.connections.send(player_id, {"type": "error", "message": "Card authoring is closed"})
            return
        if mtype == "start":
            await self._handle_start(player_id)
        elif mtype == "draw":
            if not self._is_active_player(player_id):
                await self.connections.send(player_id, {"type": "error", "message": "Not your turn"})
                return
            await self._handle_draw(player_id)
        elif mtype in ("pass", "end_turn"):
            if not self._is_active_player(player_id):
                await self.connections.send(player_id, {"type": "error", "message": "Not your turn"})
                return
            # Draw-first rule: while the deck has cards you must draw before you
            # can end your turn. (An empty deck can't be drawn from, so passing
            # is always allowed then — it's how the final turns wind down.)
            if not self._has_drawn and self.state.deck:
                await self.connections.send(
                    player_id, {"type": "error", "message": "Draw a card before ending your turn"}
                )
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
            if not self._has_drawn and self.state.deck:
                await self.connections.send(player_id, {"type": "error", "message": "Draw a card before playing"})
                return
            await self._handle_play(player_id, msg)
        elif mtype == "create_card":
            await self._handle_create_card(player_id, msg)
        elif mtype == "preview_card":
            await self._handle_preview_card(player_id, msg)
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
        exceed ``MAX_ROOM_ART_BYTES``: rooms are never evicted and mid-game card
        creation is uncapped, so the registry needs a hard cap. Callers keep the
        card, just artless (``has_art: False``).
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

    async def _start_playing(self, player_id: str | None = None) -> None:
        """setup → playing: gate on authoring, finalise deck, deal, begin play.

        ``player_id`` is the player who requested the manual start; it is used
        ONLY to address the "waiting on…" error when someone is still behind. On
        the AUTO-START path (called from ``_handle_create_card`` once everyone has
        finished authoring) there is no requesting player, so it is None and the
        gate below never fires (we only auto-start when nobody is behind).
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
        self.state = self.state.model_copy(
            update={
                "phase": "playing",
                "cards": merged_cards,
                "deck": deck,
                "players": new_players,
                # Seed the explicit turn rotation from the real players who
                # made it into this game, in seating order.
                "turn_order": [p.id for p in dealt_to],
            }
        )
        # Begin the first player's turn (draw → play → end model): no auto-draw.
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

    # ── turn lifecycle (draw → play → end turn → advance) ──
    async def _start_turn(self, player_id: str) -> None:
        """Begin ``player_id``'s turn. Resets the per-turn draw flag.

        No auto-draw: the active player must send an explicit ``draw`` before
        they may play or end their turn (while the deck is non-empty). Actual
        end-of-game timing is handled in ``_advance_turn`` (once the deck is
        exhausted the drawer finishes, then the game ends), so this method only
        resets bookkeeping and broadcasts.
        """
        self._has_drawn = False
        self._plays_this_turn = 0
        await self._emit_hooks(GameEvent.ON_TURN_START, player_id)
        await self._broadcast_state()

    async def _handle_draw(self, player_id: str) -> None:
        """Active player draws their ``rules.draw`` card(s) — the first turn step.

        Enforces one draw per turn. A draw rule of 0 (e.g. Uno-style house
        rules) satisfies the draw step without touching the deck. When the
        end condition is met right after drawing (deck_empty being the
        classic case) the end latches so the game ends after this player
        finishes their turn.
        """
        if self._has_drawn:
            await self.connections.send(player_id, {"type": "error", "message": "You have already drawn this turn"})
            return
        amount = self.state.rules.draw
        if amount <= 0:
            self._has_drawn = True
            await self._log_and_broadcast(f"{self._name(player_id)} skips drawing (draw rule is 0)")
            await self._broadcast_state()
            return
        if not self.state.deck:
            # Nothing left to draw; mark drawn so the player can still play/pass.
            self._has_drawn = True
            await self.connections.send(player_id, {"type": "error", "message": "The deck is empty — nothing to draw"})
            # Broadcast so the has_drawn flag change reaches clients and Play/Pass unlock.
            await self._broadcast_state()
            return

        self._draw_cards(player_id, amount)
        self._has_drawn = True
        await self._emit_hooks(GameEvent.ON_DRAW_STEP, player_id)
        if evaluate_end_condition(self.state):
            # Met at draw time (classically: the last card was just drawn) —
            # the game ends when this player's turn ends.
            self._deck_exhausted = True
        await self._log_and_broadcast(f"{self._name(player_id)} drew a card")
        # Push a fresh snapshot so clients see the new hand + has_drawn without a refresh.
        await self._broadcast_state()

    def _draw_cards(self, player_id: str, count: int) -> None:
        """Move up to ``count`` cards from the top of the deck into a hand (in place
        on self.state via immutable copy). Stops early if the deck runs out."""
        n = min(count, len(self.state.deck))
        if n <= 0:
            return
        drawn, rest = self.state.deck[:n], self.state.deck[n:]
        new_players = [
            p.model_copy(update={"hand": [*p.hand, *drawn]}) if p.id == player_id else p for p in self.state.players
        ]
        self.state = self.state.model_copy(update={"deck": rest, "players": new_players})

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
        self._draw_cards(player_id, amount)
        await self._log_and_broadcast(f"{self._name(player_id)} cannot play and draws {amount}")
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
        card_dict = card if isinstance(card, dict) else card.model_dump()
        plan = compile_card_plan(card_dict)
        if plan is not None and plan.steps:
            return True
        # A free-text card (description present) can still be interpreted/played.
        description = card_dict.get("description") or ""
        return bool(description.strip())

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

        Derived from the card's canonical placement/timing (preserved by bead 1):
        - placement == "center"                    → the shared table center.
        - placement == "self" AND timing=="modifier" (a persistent card that keeps
          modifying future events)                 → the player's in-play zone,
          i.e. it stays "in front of" the player.
        - everything else (immediate point cards, cards with no canonical at all,
          and authored blanks)                     → the discard pile.
        """
        canonical = card.get("canonical") if isinstance(card, dict) else getattr(card, "canonical", None)
        if canonical is None:
            # No canonical (blanks, plain point cards): resolve-and-discard.
            return "discard"
        placement = canonical.get("placement") if isinstance(canonical, dict) else getattr(canonical, "placement", None)
        timing = canonical.get("timing") if isinstance(canonical, dict) else getattr(canonical, "timing", None)
        if placement == "center":
            return "center"
        if placement == "self" and timing == "modifier":
            return "in_play"
        return "discard"

    async def _resolve_plan(self, card_id: str, card, actor_id: str | None = None) -> ResolutionPlan:
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
            result: InterpretResult = await asyncio.to_thread(
                run_agent, title, description, self.state, actor_id, creator_id=creator_id, card_id=card_id
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

        note = title or "Card"
        return ResolutionPlan(steps=[OpsStep(ops=[CustomNoteOp(note=f"Played {note} (no mechanical effect)")])])

    def _canonicalize_interpretation(self, result) -> dict:
        """Build the structured ``canonical`` payload for an interpreted card.

        Programs serialize their live ops; a triggered snippet becomes a
        register_hook authoring op (single pipeline); an immediate snippet is
        carried as canonical["snippet"] for the play path. Cards with neither
        contribute nothing (fall back to the LLM next time).
        """
        plan = result.to_plan()
        if not plan.steps:
            return {}
        canonical: dict = {"steps": [step.model_dump() for step in plan.steps]}
        ops = [op.model_dump() for step in plan.steps if isinstance(step, OpsStep) for op in step.ops]
        snippets = [step for step in plan.steps if isinstance(step, SnippetStep)]
        if ops:
            canonical["ops"] = ops
        if len(snippets) == 1 and isinstance(plan.steps[-1], SnippetStep):
            canonical["snippet"] = snippets[0].code
        return {"canonical": canonical}

    async def _execute_plan(
        self,
        base_state: GameState,
        plan: ResolutionPlan,
        ctx: HookContext,
        card,
    ) -> GameState:
        from config import get_settings
        from engine.sandbox.revalidate import apply_snippet_diff
        from engine.sandbox.runner import execute_snippet

        card_id = ctx.card_id or ""
        destination = self._play_destination(card)
        working = base_state.move_card(
            card_id,
            "hand",
            destination,
            from_player_id=ctx.actor_id,
            to_player_id=ctx.actor_id,
        )
        rng = random.Random()
        ctx_dict = {
            "actor_id": ctx.actor_id,
            "event": str(ctx.event),
            "card_id": ctx.card_id,
            "amount": ctx.amount,
        }
        for step in plan.steps:
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
            await self.connections.send(player_id, {"type": "error", "message": f"Play rejected: {veto}"})
            await self._log_and_broadcast(
                f"[rule] {self._name(player_id)}'s {self._card_title(card)} was rejected: {veto}"
            )
            await self._apply_cannot_play(player_id)
            return

        title = card["title"] if isinstance(card, dict) else getattr(card, "title", "")
        plan = await self._resolve_plan(card_id, card, actor_id=player_id)
        game_ending = False
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
        before = {p.id: p.score for p in self.state.players}
        try:
            self.state = await self._execute_plan(self.state, plan, ctx, card)
        except Exception as exc:
            logger.warning("resolution plan failed for %s: %s", card_id, exc)
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
            await self._log_and_broadcast(self._describe_play(player_id, card, before))
            await self._emit_hooks(GameEvent.ON_PLAY, player_id, card_id=card_id)
            game_ending = self._end_now() or win_condition_met(self.state)

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

    async def _handle_create_card(self, player_id: str, msg) -> None:
        """Author a new card (allowed off-turn / during setup).

        During ``setup`` we DO NOT call the LLM: authored cards are interpreted
        deterministically (via ``compile_card``) or best-effort at play time, so
        setup authoring stays fast and never depends on a live service. The card
        is simply registered with its ``creator_id`` (which drives
        ``setup_progress`` and the start gate) and broadcast.

        Setup authoring is capped at ``CARDS_TO_AUTHOR`` per player: the start
        gate only enforces a LOWER bound, so without this cap a player could
        author unlimited cards before the host starts. The cap is scoped STRICTLY
        to ``phase == "setup"`` — mid-game a player may freely create cards (e.g.
        blanks), so the playing-phase path below stays uncapped.

        Once this card completes the LAST player's authoring quota, the game
        AUTO-STARTS (setup→playing) — no manual "start" is required.
        """
        # Setup-only upper bound: reject (targeted, not broadcast) once the player
        # has authored the required number of cards, BEFORE any card is created.
        if self.state.phase == "setup" and self._authored_count(player_id) >= CARDS_TO_AUTHOR:
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
            },
        }
        self.state = self.state.model_copy(update={"cards": new_cards})

        if self.state.phase == "setup":
            # Auto-start: once every non-spectator has authored the required
            # number of cards, transition straight to playing — no manual
            # "start" needed. Guard the degenerate zero-players case so we never
            # auto-start an empty table. ``_start_playing`` broadcasts the new
            # (playing) state itself, so we don't also broadcast the pre-start
            # setup state on this path.
            real_players = self.state.turn_players()
            everyone_done = bool(real_players) and all(
                self._authored_count(p.id) >= CARDS_TO_AUTHOR for p in real_players
            )
            if everyone_done:
                await self._start_playing()
            else:
                await self._broadcast_state()
            return

        from agent.contract import InterpretResult
        from agent.runtime import run_agent

        await self.connections.broadcast({"type": "brewing", "card_id": card_id})
        # A just-created card's actor IS its creator (player_id authored it).
        try:
            result: InterpretResult = await asyncio.to_thread(
                run_agent, msg.title, msg.description, self.state, player_id, creator_id=player_id, card_id=card_id
            )
        except Exception:
            logger.exception("run_agent failed for %s; using deterministic fallback", card_id)
            result = InterpretResult(verdict="invalid", comment="", persona_action="none")

        # Store the interpretation on the card: human-readable summary fields
        # plus STRUCTURED canonical ops so the card replays deterministically
        # (this game and, if kept, every future game — no LLM round-trip).
        card = {
            **self.state.cards[card_id],
            "program": str(result.program) if result.program is not None else None,
            "snippet": getattr(result.snippet, "code", None),
            "verdict": result.verdict,
            **self._canonicalize_interpretation(result),
        }
        merged = {**self.state.cards, card_id: card}
        self.state = self.state.model_copy(update={"cards": merged})

        await self.connections.broadcast(
            {
                "type": "card_interpreted",
                "card_id": card_id,
                "program": card["program"],
                "snippet": card["snippet"],
                "verdict": card["verdict"],
                # Carry the in-character comment for the frontend/D1 (not persisted here).
                "comment": result.comment,
            }
        )
        # D1: persist the arbiter comment so it survives a reconnect (see
        # _resolve_plan). create_card interprets a card_id exactly once, so no
        # round-trip guard is needed, but we route through the same helper for a
        # single consistent format + prefix.
        await self._log_agent_comment(card_id, result.comment)
        await self._broadcast_state()

    async def _handle_preview_card(self, player_id: str, msg) -> None:
        await self.connections.send(
            player_id,
            {"type": "preview_result", "program": None, "snippet": msg.description, "verdict": "ok"},
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
        - ``has_drawn`` — whether the active player has taken their draw step
          this turn (client gates Draw vs Play/End-turn).
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
