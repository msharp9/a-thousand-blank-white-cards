"""board.rooms.room — one game session: GameState + ConnectionManager + turn enforcement.

Room owns an immutable GameState (replaced on each mutation) and serialises all
handle_action calls with an asyncio.Lock so concurrent WebSocket messages cannot
corrupt turn order. Play/pass require the active player's turn. Setup card
creation/revision and preview are SETUP-ONLY; submitted setup cards are drafted
off-lock immediately, while the only mid-game authoring is filling in a blank
as it is played (author-on-play, see _handle_play). Play runs the agent
interpretation graph via asyncio.to_thread when no compiled plan exists,
applying resulting effects through the engine.

Turn model (auto-draw → play → end turn): drawing is AUTOMATIC. When a turn
begins (``_start_turn``) the server draws ``rules.draw`` card(s) for the new
active player — there is no client ``draw`` message. The player then ``play``s
a card OR ``pass``es / ``end_turn``s to end without playing. Either ending
advances the turn; the next player is auto-drawn to in the same way. Cards
carrying the ``play_on_draw`` attribute never rest in a hand: choke-point scans
after the auto-draw and after every play's accounting tail auto-play them for
their holder at no action cost (see ``_process_play_on_draw``).

End game: there are TWO distinct end paths with distinct timing.

- Deck exhaustion: when a player draws the LAST card of the deck,
  ``_deck_exhausted`` latches. That player finishes their turn normally; only
  once their turn ends (``_advance_turn``) does the game end (Per the rules:
  the player who draws the last card completes their turn, then the game
  ends). The same deferred timing applies when a play's EFFECT (not a draw)
  empties the deck — e.g. milling or exiling the remaining cards:
  ``_after_play_effects`` latches ``_deck_exhausted`` when the deck went from
  non-empty to empty across the play, so emptying the deck never ends the game
  mid-turn.
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
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from engine.apply import apply_effect
from engine.compile import compile_card_plan
from engine.events import EventBus, GameEvent, HookContext
from engine.hooks import build_registry, collect_hook_errors
from engine.history import append_history_event, fallback_counts, record_draw, record_game_end
from engine.loop import advance_turn, tick_condition_ttls
from engine.reducers import collect_hand_reveals
from engine.scoring import evaluate_end_condition, evaluate_win_condition, resolve_end_of_game, win_condition_met
from models.admin import PendingAdminProposal
from models.card import MAX_ROOM_ART_BYTES, hoist_static_attributes
from models.effects import (
    AddPointsOp,
    CounterPlayOp,
    CustomNoteOp,
    DestroyCardOp,
    DrawCardsOp,
    EffectProgram,
    InteractionStep,
    Op,
    OpsStep,
    ResolutionPlan,
    SetPointsOp,
    SnippetStep,
)
from models.game_state import EndCondition, EpilogueCardOutcome, EpilogueResultSummary, GameState, Player, Spectator
from models.interactions import (
    CardOrderInteraction,
    CardOrderResponse,
    CardPickInteraction,
    CardPickResponse,
    ChoiceInteraction,
    ChoiceResponse,
    ConfirmInteraction,
    ConfirmResponse,
    MAX_INTERACTION_DESCRIPTOR_BYTES,
    MAX_OPTION_PAYLOAD_BYTES,
    DrawingInteraction,
    DrawingResponse,
    InteractionDescriptor,
    InteractionProgress,
    InteractionResponsePayload,
    NumberInteraction,
    NumberResponse,
    TextInteraction,
    TextResponse,
    compact_drawing_preview,
)
from board.rooms.interactions import PendingResolution
from board.rooms.admin import apply_admin_actions
from board.rooms.choices import chosen_card_candidates, plan_choice_needs
from board.rooms.connections import ConnectionManager
from board.rooms.deck import (
    BLANKS_PER_PLAYER,
    PREMADE_POOL_SIZE,
    build_premade_pool,
    finalize_deck,
)
from board.rooms.epilogue import EpilogueManager
from board.rooms.redaction import redact_snapshot

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

# The synthetic hand-limit discard plan (rules.hand_limit enforcement at end of
# turn). The result_key names the CardPickInteraction's collected picks; the
# timeout bounds how long an over-limit player may stall the table before the
# hand tail is discarded for them.
HAND_LIMIT_RESULT_KEY = "hand_limit_discards"
HAND_LIMIT_TIMEOUT_SECONDS = 60

# Headroom kept when budgeting dynamic-choice option payloads: the descriptor
# cap is measured on pydantic's compact JSON while payloads are budgeted with
# json.dumps' spaced encoding, so the margin absorbs any residual drift.
_PREVIEW_SERIALIZATION_MARGIN = 1_024

# Floor below which the preview budget stops halving; a budget this small means
# even a minimal one-stroke preview cannot fit and the plan must fall back.
_MIN_PREVIEW_BUDGET = 256

# A host correction needs unanimous consent from every other seated player.
ADMIN_PROPOSAL_TIMEOUT_SECONDS = 60

# Recursion guard for play_on_draw auto-plays: the most cards a single turn's
# chain may auto-play (a pod card drawing pod cards drawing pod cards…). Once
# hit, further pod cards stay in hand until a later turn's scan.
MAX_AUTO_PLAYS_PER_TURN = 3

# Setup authoring stays responsive while interpretation runs. Bound per-room
# concurrency so a table cannot fan out an unbounded number of LLM calls.
CARD_DRAFT_CONCURRENCY = 2


class TurnTimer:
    """Pausable countdown for the active player's turn (rules.turn_timer).

    REMAINING-SECONDS accounting: the reaction/interaction timers are
    absolute-deadline sleeps that can only be cancelled, so they cannot pause.
    This one banks ``deadline - now`` on :meth:`pause` and re-arms with the
    banked remainder on :meth:`resume`, so time the room spends suspended
    (brewing an interpretation, a reaction window, an interaction) never
    counts against the player. ``generation`` defeats stale-expiry races the
    way ``window_id`` does for reaction windows: every (re)arm or disarm bumps
    it, and the expiry callback re-checks its own generation under the room
    lock before acting.
    """

    def __init__(self, on_expire: Callable[[int], Awaitable[None]]) -> None:
        self._on_expire = on_expire
        self.generation: int = 0
        self.player_id: str | None = None
        self._task: asyncio.Task | None = None
        self._deadline: float | None = None  # epoch seconds while running
        self._remaining: float | None = None  # banked seconds while paused

    @property
    def running(self) -> bool:
        return self._deadline is not None

    @property
    def paused(self) -> bool:
        return self._remaining is not None

    @property
    def deadline_epoch_ms(self) -> int | None:
        return int(self._deadline * 1000) if self._deadline is not None else None

    def start(self, seconds: float, player_id: str) -> None:
        """Arm a fresh clock for ``player_id``, replacing any previous one."""
        self.cancel()
        self.player_id = player_id
        self._arm(seconds)

    def pause(self) -> bool:
        """Bank the remaining time and stop the task. False when not running."""
        if self._deadline is None:
            return False
        self._remaining = max(0.0, self._deadline - time.time())
        self._disarm()
        return True

    def resume(self) -> bool:
        """Re-arm with the banked remainder. False when not paused."""
        if self._remaining is None:
            return False
        self._arm(self._remaining)
        return True

    def cancel(self) -> None:
        self._disarm()
        self._remaining = None
        self.player_id = None

    def finish(self) -> None:
        """Expiry-handler cleanup: drop all clock state WITHOUT cancelling the
        task (the handler runs inside it; cancelling would abort the forced
        end-turn at its next await)."""
        self.generation += 1
        self._task = None
        self._deadline = None
        self._remaining = None

    def _arm(self, seconds: float) -> None:
        self.generation += 1
        self._remaining = None
        self._deadline = time.time() + seconds
        self._task = asyncio.create_task(self._run(self.generation, seconds))

    def _disarm(self) -> None:
        self.generation += 1
        if self._task is not None and self._task is not asyncio.current_task() and not self._task.done():
            self._task.cancel()
        self._task = None
        self._deadline = None

    async def _run(self, generation: int, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        await self._on_expire(generation)


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
    # False for a play_on_draw auto-play suspended behind the window: on
    # commit it must not consume the owner's play allowance or advance the turn.
    count_as_play: bool = True


@dataclass
class PendingAutoPlay:
    """A play_on_draw auto-play waiting on its owner's prompt_choice answer.

    Transient like ``PendingPlay`` (a Room attribute, never persisted): the
    room does NOT freeze while it waits — the owner's follow-up ``play``
    message for this card is routed to :meth:`Room._resume_auto_play` instead
    of the normal play gates, and the card is excluded from further auto-play
    scans while pending. ``chosen_*`` accumulate across follow-ups the same
    way the normal two-prompt (player then card) flow does.
    """

    owner_id: str
    card_id: str
    plan: ResolutionPlan
    correlation_id: str
    chosen_player_id: str | None = None
    chosen_card_id: str | None = None


class Room:
    """One game session. Thread-safe via asyncio.Lock."""

    def __init__(
        self,
        code: str,
        mode: str = "both",
        *,
        turn_timer: int | None = None,
        on_change: Callable[[Room], None] | None = None,
    ) -> None:
        self.code = code
        self.state: GameState = GameState(room_code=code, mode=mode)
        # Host-chosen per-turn time limit (seconds). Stored as rules.turn_timer
        # so it rides snapshots and set_rule can rewrite/lift it mid-game.
        if turn_timer is not None:
            self.state = self.state.model_copy(
                update={"rules": self.state.rules.model_copy(update={"turn_timer": turn_timer})}
            )
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
        # Per-turn bookkeeping for the auto-draw→play→end model. Reset at the
        # start of every turn (see _start_turn). ``_has_drawn`` records the
        # turn's auto-draw for the client snapshot; ``_deck_exhausted`` latches
        # once the last card is drawn so the game ends after the drawer
        # finishes their turn.
        self._has_drawn: bool = False
        self._plays_this_turn: int = 0
        self._deck_exhausted: bool = False
        # High-water mark of dice_roll history sequences already broadcast as
        # immediacy pushes (see _push_dice_rolls). Restored rooms start at the
        # current history tip so reconnects never replay old roll animations.
        self._dice_seq_pushed: int = 0
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
        # (card_id, kind) pairs already reported to the triage agent — one triage
        # report per distinct failure mode per card (see _report_failure_for_triage;
        # gated by Settings.triage_agent_dedupe).
        self._reported_failures: set[tuple[str, str]] = set()
        # Interpretation RunMetrics per card (serialized dicts), captured in
        # _resolve_plan for the triage agent, popped when a failure report
        # consumes them; cleared at turn start to bound growth.
        self._last_run_metrics: dict[str, dict] = {}
        self._pending_resolution: PendingResolution | None = None
        self._interaction_timer: asyncio.Task | None = None
        self._pending_admin: PendingAdminProposal | None = None
        self._admin_timer: asyncio.Task | None = None
        # Spectator hosts receive privileged card state only while their Host
        # panel is explicitly open. This is connection/session state, never
        # persisted game state.
        self._admin_viewers: set[str] = set()
        # The pausable per-turn clock (rules.turn_timer). Transient like the
        # reaction/interaction timers: a restart re-arms a fresh full clock
        # (see ensure_pending_timeout) rather than persisting the remainder.
        self._turn_timer = TurnTimer(self._turn_timer_expired)
        # Card id of the play currently being interpreted/resolved (brewing),
        # or None. Set/cleared (try/finally) around the play branch in
        # _dispatch and checked BEFORE waiting on the lock in handle_action —
        # see handle_action for why the check must precede the lock.
        self._resolving_play: str | None = None
        # The play currently suspended behind an open reaction window, or None.
        # Transient by design — see PendingPlay.
        self._pending: PendingPlay | None = None
        # play_on_draw bookkeeping (see _process_play_on_draw). The counter and
        # deferred set reset every turn; the prompt survives turn boundaries so
        # a slow owner can still answer.
        self._auto_plays_this_turn: int = 0
        self._auto_play_deferred: set[str] = set()
        self._pending_auto_play: PendingAutoPlay | None = None
        # Set when a counted play's turn decision (advance/end) had to be
        # deferred because its auto-play chain suspended on a reaction window
        # or interaction; the chain's completion runs the decision.
        self._advance_after_auto_play: bool = False
        # Setup-authored cards are interpreted off-lock. Each task captures the
        # card revision/correlation id and applies its result under the room lock,
        # so a stale completion can never overwrite a retry.
        self._card_draft_tasks: dict[str, asyncio.Task] = {}
        self._card_draft_semaphore = asyncio.Semaphore(CARD_DRAFT_CONCURRENCY)
        # Suppresses normal auto-start while the DEV skip-setup endpoint waits
        # for already-submitted drafts and fills the remaining slots with blanks.
        self._dev_skip_in_progress = False

    # ── player management ──
    def add_player(self, player_id: str, name: str) -> None:
        """Append a real player to the immutable GameState (reassigns self.state)."""
        new_players = [*self.state.players, Player(id=player_id, name=name)]
        self.state = self.state.model_copy(
            update={
                "players": new_players,
                "host_id": self.state.host_id or player_id,
            }
        )

    def add_spectator(self, player_id: str, name: str) -> None:
        """Append a spectator (late joiner) to the immutable GameState.

        Spectators live in ``state.spectators``, not ``players``: they take no
        turn, are never dealt/auto-drawn to, cannot author or play cards, and
        are excluded from win scoring — structurally, not by a guard. The join
        *policy* (who becomes a spectator) lives in :meth:`RoomManager.join`,
        which decides from the room's phase; this method just records it.
        """
        new_spectators = [*self.state.spectators, Spectator(id=player_id, name=name)]
        self.state = self.state.model_copy(
            update={
                "spectators": new_spectators,
                "host_id": self.state.host_id or player_id,
            }
        )

    def get_player_ids(self) -> list[str]:
        """All ids that may open a WebSocket for this room: players + spectators."""
        return [p.id for p in self.state.players] + [s.id for s in self.state.spectators]

    def _is_spectator(self, player_id: str) -> bool:
        return self.state.is_spectator(player_id)

    def _is_host(self, player_id: str) -> bool:
        return self.state.host_id == player_id

    def _is_god_host(self, participant_id: str) -> bool:
        return self._is_host(participant_id) and self._is_spectator(participant_id)

    def clear_admin_view(self, participant_id: str) -> None:
        """Drop one transient privileged Host-panel subscription."""
        self._admin_viewers.discard(participant_id)

    async def _handle_lobby_set_host(self, player_id: str, msg) -> None:
        if self.state.phase != "lobby":
            await self.connections.send(player_id, {"type": "error", "message": "Host changes are lobby-only"})
            return
        if not self._is_host(player_id):
            await self.connections.send(player_id, {"type": "error", "message": "Only the host can transfer hosting"})
            return
        if not self.state.has_participant(msg.participant_id):
            await self.connections.send(player_id, {"type": "error", "message": "Participant not found"})
            return
        if self.state.host_id == msg.participant_id:
            return
        self.state = self.state.model_copy(update={"host_id": msg.participant_id})
        await self._broadcast_state()

    async def _handle_lobby_set_role(self, player_id: str, msg) -> None:
        if self.state.phase != "lobby":
            await self.connections.send(player_id, {"type": "error", "message": "Role changes are lobby-only"})
            return
        if not self._is_host(player_id):
            await self.connections.send(player_id, {"type": "error", "message": "Only the host can assign roles"})
            return
        if not self.state.has_participant(msg.participant_id):
            await self.connections.send(player_id, {"type": "error", "message": "Participant not found"})
            return

        is_spectator = self.state.is_spectator(msg.participant_id)
        if (msg.role == "spectator") == is_spectator:
            return
        if msg.role == "spectator":
            if len(self.state.players) <= 1:
                await self.connections.send(
                    player_id,
                    {"type": "error", "message": "At least one player must remain in the game"},
                )
                return
            participant = self.state.get_player(msg.participant_id)
            players = [candidate for candidate in self.state.players if candidate.id != msg.participant_id]
            spectators = [*self.state.spectators, Spectator(id=participant.id, name=participant.name)]
        else:
            participant = self.state.get_spectator(msg.participant_id)
            players = [*self.state.players, Player(id=participant.id, name=participant.name)]
            spectators = [candidate for candidate in self.state.spectators if candidate.id != msg.participant_id]
        self.state = self.state.model_copy(update={"players": players, "spectators": spectators})
        await self._broadcast_state()

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
    FROZEN_WHILE_RESOLVING = frozenset({"start", "pass", "end_turn", "play", "create_card", "admin_propose"})

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
        # epilogue_vote/epilogue_done are blocked for every spectator, host
        # included — only seated players decide a card's fate — while a
        # spectator host keeps the start/finalize controls below.
        spectator_actions = {
            "start",
            "lobby_set_host",
            "lobby_set_role",
            "pass",
            "end_turn",
            "play",
            "pass_reaction",
            "create_card",
            "redraft_card",
            "preview_card",
            "interaction_response",
            "admin_propose",
            "admin_vote",
            "admin_cancel",
            "admin_view",
            "epilogue_start",
            "epilogue_finalize",
            "epilogue_vote",
            "epilogue_done",
        }
        spectator_host_actions = {
            "start",
            "lobby_set_host",
            "lobby_set_role",
            "admin_propose",
            "admin_cancel",
            "admin_view",
            "epilogue_start",
            "epilogue_finalize",
        }
        if (
            self._is_spectator(player_id)
            and mtype in spectator_actions
            and not (self._is_host(player_id) and mtype in spectator_host_actions)
        ):
            await self.connections.send(player_id, {"type": "error", "message": "Spectators cannot take game actions"})
            return
        if self._pending_admin is not None and mtype not in {"admin_vote", "admin_cancel", "admin_view"}:
            await self.connections.send(
                player_id,
                {"type": "error", "message": "Waiting for the table to vote on the host's proposal"},
            )
            return
        if mtype == "lobby_set_host":
            await self._handle_lobby_set_host(player_id, msg)
            return
        if mtype == "lobby_set_role":
            await self._handle_lobby_set_role(player_id, msg)
            return
        if mtype == "admin_view":
            await self._handle_admin_view(player_id, msg)
            return
        if mtype == "admin_propose":
            await self._handle_admin_propose(player_id, msg)
            return
        if mtype == "admin_vote":
            await self._handle_admin_vote(player_id, msg)
            return
        if mtype == "admin_cancel":
            await self._handle_admin_cancel(player_id, msg)
            return
        # Authoring gate: create_card/preview_card exist ONLY during setup
        # (each player writes their quota). The one mid-game authoring path is
        # playing a blank, which rides the `play` message (author-on-play).
        if mtype in {"create_card", "redraft_card", "preview_card"} and self.state.phase != "setup":
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
            # non-reaction actions. Cleared unconditionally. The turn clock
            # pauses for the brew's duration (and stays paused while the
            # window itself remains open — _maybe_resume_turn_timer no-ops).
            self._resolving_play = msg.card_id
            await self._pause_turn_timer()
            try:
                await self._handle_reaction_play(player_id, msg)
            finally:
                self._resolving_play = None
                await self._maybe_resume_turn_timer()
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
        if self._dev_skip_in_progress and mtype in {"start", "create_card", "redraft_card", "preview_card"}:
            await self.connections.send(
                player_id,
                {"type": "error", "message": "Development skip-setup is already finishing this setup"},
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
            # A prompt_choice follow-up for a suspended play_on_draw auto-play
            # is routed by (owner, card_id) — it bypasses the active-player and
            # play-allowance gates because the auto-play costs no action.
            pending_auto = self._pending_auto_play
            if pending_auto is not None and player_id == pending_auto.owner_id and msg.card_id == pending_auto.card_id:
                await self._resume_auto_play(player_id, msg)
                return
            if not self._is_active_player(player_id):
                await self.connections.send(player_id, {"type": "error", "message": "Not your turn"})
                return
            # Freeze the room's game actions for the whole play (author-on-play
            # → veto → interpretation → execution → turn accounting); cleared
            # unconditionally so a crashing LLM call/plan can never leave the
            # room frozen. handle_action rejects against this flag pre-lock.
            # The turn clock pauses for the same span — brewing must not cost
            # the player time — and resumes only if the play left the turn
            # running (a turn advance re-arms a fresh clock instead).
            self._resolving_play = msg.card_id
            await self._pause_turn_timer()
            try:
                await self._handle_play(player_id, msg)
            finally:
                self._resolving_play = None
                await self._maybe_resume_turn_timer()
        elif mtype == "create_card":
            await self._handle_create_card(player_id, msg)
        elif mtype == "redraft_card":
            await self._handle_redraft_card(player_id, msg)
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
    def _setup_cards_for(self, player_id: str) -> list[tuple[str, dict]]:
        return [
            (cid, card)
            for cid, card in self.state.cards.items()
            if isinstance(card, dict)
            and card.get("origin") == "authored"
            and card.get("creator_id") == player_id
            and card.get("draft_status") is not None
        ]

    @staticmethod
    def _draft_ready(card: dict) -> bool:
        plan = compile_card_plan(card)
        return card.get("draft_status") == "ready" and plan is not None and bool(plan.steps)

    def _authored_count(self, player_id: str) -> int:
        """Number of executable setup cards authored by ``player_id``."""
        return sum(1 for _, card in self._setup_cards_for(player_id) if self._draft_ready(card))

    def _setup_slot_count(self, player_id: str) -> int:
        """Number of stable setup-authoring slots used by ``player_id``."""
        return len(self._setup_cards_for(player_id))

    def _setup_progress(self) -> dict[str, int]:
        """Map non-spectator player id -> ready-card count, for compatibility."""
        return {p.id: self._authored_count(p.id) for p in self.state.turn_players()}

    def _setup_draft_progress(self) -> dict[str, dict[str, int]]:
        progress: dict[str, dict[str, int]] = {}
        for player in self.state.turn_players():
            cards = [card for _, card in self._setup_cards_for(player.id)]
            progress[player.id] = {
                "ready": sum(self._draft_ready(card) for card in cards),
                "drafting": sum(card.get("draft_status") == "drafting" for card in cards),
                "failed": sum(card.get("draft_status") == "failed" for card in cards),
                "total": len(cards),
            }
        return progress

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

    def _replace_card_art(self, card_id: str, art: str) -> bool:
        previous = self.card_art.get(card_id)
        previous_size = len(previous) if previous else 0
        if self._card_art_bytes - previous_size + len(art) > MAX_ROOM_ART_BYTES:
            return False
        self.card_art[card_id] = art
        self._card_art_bytes = self._card_art_bytes - previous_size + len(art)
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
        - From **setup**: gate on every non-spectator having
          ``CARDS_TO_AUTHOR`` executable card drafts; then finalise the deck
          (pre-made + authored + ``BLANKS_PER_PLAYER`` blanks per player), shuffle, deal
          ``STARTING_HAND_SIZE`` to each real player, enter ``phase="playing"``
          and begin the first turn.

        The setup→playing transition normally happens AUTOMATICALLY once every
        player's final draft becomes ready (see ``_run_card_draft``); this
        manual entry is kept as a safety/fallback path (and still owns
        lobby→setup). A
        manual ``start`` that arrives after auto-start already fired lands in the
        ``else`` branch below as a harmless "Game already started" no-op.

        Deck building never requires a live external service; it runs in a thread
        since collection may touch the (in-memory) RAG store.
        """
        if not self._is_host(player_id):
            await self.connections.send(player_id, {"type": "error", "message": "Only the host can start the game"})
            return
        if not self.state.players:
            await self.connections.send(player_id, {"type": "error", "message": "At least one player is required"})
            return
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
        )
        # Pre-made cards live in the registry AND (as ids) in the deck so the
        # setup UI can render "the deck so far". They're re-shuffled with the
        # authored + blank cards at finalisation. Cards kept from a prior game
        # may carry a transient "art" key (see deck._normalise_card) — absorb it
        # into the out-of-band registry before the dicts land in GameState.
        merged_cards = {**self._absorb_card_art(cards), **self.state.cards}
        self.state = self.state.model_copy(update={"phase": "setup", "cards": merged_cards, "deck": list(pool)})
        await self._broadcast_state()

    async def _start_playing(
        self,
        player_id: str | None = None,
        *,
        rng: random.Random | None = None,
        additional_blanks: int = 0,
        bypass_setup_gate: bool = False,
    ) -> None:
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
        behind = [] if bypass_setup_gate else [p for p in dealt_to if self._authored_count(p.id) < CARDS_TO_AUTHOR]
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
            and (not isinstance(c, dict) or self._draft_ready(c))
            and (c.get("creator_id") if isinstance(c, dict) else getattr(c, "creator_id", None))
            in {p.id for p in dealt_to}
        ]

        blank_cards, deck = await asyncio.to_thread(
            finalize_deck,
            premade_ids,
            authored_ids,
            len(dealt_to),
            blanks_per_player=BLANKS_PER_PLAYER,
            additional_blanks=additional_blanks,
            blank_namespace=self.code,
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

    def _schedule_card_draft(self, card_id: str) -> None:
        existing = self._card_draft_tasks.get(card_id)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(self._run_card_draft(card_id))
        self._card_draft_tasks[card_id] = task

        def discard(done: asyncio.Task) -> None:
            if self._card_draft_tasks.get(card_id) is done:
                self._card_draft_tasks.pop(card_id, None)

        task.add_done_callback(discard)

    def _draft_interpretation_state(self, card_id: str, actor_id: str) -> GameState:
        state = self.state.model_copy(deep=True)
        players = [
            player.model_copy(update={"hand": [*player.hand, card_id]})
            if player.id == actor_id and card_id not in player.hand
            else player
            for player in state.players
        ]
        return state.model_copy(update={"players": players})

    async def _run_card_draft(self, card_id: str) -> None:
        async with self._card_draft_semaphore:
            async with self._lock:
                card = self.state.cards.get(card_id)
                if self.state.phase != "setup" or not isinstance(card, dict) or card.get("draft_status") != "drafting":
                    return
                revision = int(card.get("draft_revision", 1))
                correlation_id = str(card.get("draft_correlation_id") or uuid.uuid4())
                actor_id = str(card.get("creator_id"))
                title = str(card.get("title") or "")
                description = str(card.get("description") or "")
                art = self.card_art.get(card_id)
                draft_state = self._draft_interpretation_state(card_id, actor_id)

            from agent.contract import InterpretResult
            from agent.runtime import run_agent

            try:
                result: InterpretResult = await asyncio.to_thread(
                    run_agent,
                    title,
                    description,
                    draft_state,
                    actor_id,
                    creator_id=actor_id,
                    card_id=card_id,
                    card_art=art,
                    draft_mode=True,
                )
            except Exception:
                logger.exception("setup card draft failed unexpectedly for %s", card_id)
                result = InterpretResult(verdict="invalid", agent_error=True)

            async with self._lock:
                current = self.state.cards.get(card_id)
                if (
                    self.state.phase != "setup"
                    or not isinstance(current, dict)
                    or current.get("draft_status") != "drafting"
                    or int(current.get("draft_revision", 1)) != revision
                    or current.get("draft_correlation_id") != correlation_id
                ):
                    return

                plan = result.to_plan()
                merged = {
                    **current,
                    **self._canonicalize_interpretation(result, title=title, description=description),
                    "verdict": result.verdict,
                    "agent_comment": result.comment,
                }
                compiled = compile_card_plan(merged)
                ready = result.verdict == "ok" and bool(plan.steps) and compiled is not None and bool(compiled.steps)
                if ready:
                    merged["draft_status"] = "ready"
                    merged["draft_reason"] = None
                else:
                    for key in ("canonical", "ops", "sandbox", "attributes", "agent_comment"):
                        merged.pop(key, None)
                    merged["draft_status"] = "failed"
                    merged["draft_reason"] = (
                        "The drafting service failed; retry this card."
                        if result.agent_error
                        else "The arbiter could not build executable mechanics; revise or retry this card."
                    )

                self.state = self.state.model_copy(update={"cards": {**self.state.cards, card_id: merged}})
                self._notify_change()
                # This revision is terminal before we broadcast it. Remove the
                # current task from the slot now so a client that immediately
                # retries a freshly-failed card can schedule the next revision;
                # the old task's done callback is identity-guarded and cannot
                # remove that replacement.
                current_task = asyncio.current_task()
                if self._card_draft_tasks.get(card_id) is current_task:
                    self._card_draft_tasks.pop(card_id, None)

                everyone_ready = bool(self.state.turn_players()) and all(
                    self._authored_count(player.id) >= CARDS_TO_AUTHOR for player in self.state.turn_players()
                )
                if ready and everyone_ready and not self._dev_skip_in_progress:
                    await self._start_playing()
                else:
                    await self._broadcast_state()
                    if not ready:
                        await self.connections.send(
                            actor_id,
                            {
                                "type": "error",
                                "message": f"{title} needs revision before it can join the deck.",
                            },
                        )

    async def wait_for_card_drafts(self) -> None:
        """Wait until every currently scheduled setup draft reaches a terminal state."""
        while True:
            tasks = [task for task in self._card_draft_tasks.values() if not task.done()]
            if not tasks:
                return
            await asyncio.gather(*tasks, return_exceptions=True)

    def ensure_card_drafts(self) -> None:
        """Resume persisted setup cards that were drafting across a dev reload."""
        if self.state.phase != "setup":
            return
        player_ids = {player.id for player in self.state.turn_players()}
        deck_ids = set(self.state.deck)
        cards = dict(self.state.cards)
        changed = False
        to_schedule: list[str] = []
        for card_id, original in self.state.cards.items():
            if (
                not isinstance(original, dict)
                or original.get("origin") != "authored"
                or original.get("creator_id") not in player_ids
                or card_id in deck_ids
            ):
                continue
            card = dict(original)
            status = card.get("draft_status")
            if status is None:
                plan = compile_card_plan(card)
                card["draft_status"] = "ready" if plan is not None and plan.steps else "drafting"
                card["draft_revision"] = 1
                card["draft_correlation_id"] = str(uuid.uuid4())
                status = card["draft_status"]
                cards[card_id] = card
                changed = True
            if status == "drafting":
                to_schedule.append(card_id)
        if changed:
            self.state = self.state.model_copy(update={"cards": cards})
            self._notify_change()
        for card_id in to_schedule:
            self._schedule_card_draft(card_id)

    async def cancel_card_drafts(self) -> None:
        tasks = list(self._card_draft_tasks.values())
        self._card_draft_tasks.clear()
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._admin_timer is not None and not self._admin_timer.done():
            self._admin_timer.cancel()

    async def dev_autofill_authoring(self) -> None:
        """DEV shortcut: wait for submitted drafts, then fill missing slots with blanks."""
        async with self._lock:
            if self.state.phase not in ("lobby", "setup"):
                raise ValueError("game already started")
            if self._dev_skip_in_progress:
                raise ValueError("skip setup already in progress")
            self._dev_skip_in_progress = True
            if self.state.phase == "lobby":
                await self._enter_setup()
            self._notify_change()

        try:
            await self.wait_for_card_drafts()
            async with self._lock:
                if self.state.phase != "setup":
                    return
                missing = sum(
                    max(0, CARDS_TO_AUTHOR - self._authored_count(player.id)) for player in self.state.turn_players()
                )
                unusable_ids = [
                    card_id
                    for player in self.state.turn_players()
                    for card_id, card in self._setup_cards_for(player.id)
                    if not self._draft_ready(card)
                ]
                if unusable_ids:
                    cards = {cid: card for cid, card in self.state.cards.items() if cid not in unusable_ids}
                    self.state = self.state.model_copy(update={"cards": cards})
                    for card_id in unusable_ids:
                        art = self.card_art.pop(card_id, None)
                        if art:
                            self._card_art_bytes -= len(art)
                await self._start_playing(
                    additional_blanks=missing,
                    bypass_setup_gate=True,
                )
                self._notify_change()
        finally:
            self._dev_skip_in_progress = False

    # ── turn lifecycle (auto-draw → play → end turn → advance) ──
    async def _start_turn(self, player_id: str) -> None:
        """Begin ``player_id``'s turn: tick their expiring condition TTLs,
        reset per-turn bookkeeping, auto-draw their ``rules.draw`` card(s),
        broadcast the fresh snapshot.

        Every turn — including the very first at the setup→playing transition —
        starts here, so the auto-draw is uniform. _start_turn is only ever
        called outside interaction barriers / reaction windows (from
        ``_start_playing`` and ``_advance_turn``), so the draw can never
        interleave with a suspended play. End-of-game timing is handled in
        ``_advance_turn`` (once the deck is exhausted the drawer finishes,
        then the game ends).

        ON_TURN_START / ON_DRAW_STEP hooks may eliminate the very player whose
        turn is starting (a landmine/poison rule); their turn cannot proceed,
        so it ends immediately via ``_advance_turn`` — mirroring the
        eliminated-active-player handling in ``_turn_decision``. A stale
        auto-play prompt from a force-ended turn is dropped here; its card is
        still in hand, so this turn's scan re-prompts fresh.
        """
        self._has_drawn = False
        self._plays_this_turn = 0
        self._auto_plays_this_turn = 0
        self._auto_play_deferred.clear()
        self._advance_after_auto_play = False
        self._pending_auto_play = None
        self._last_run_metrics.clear()
        self.state = tick_condition_ttls(self.state, player_id)
        await self._arm_turn_timer(player_id)
        await self._emit_hooks(GameEvent.ON_TURN_START, player_id)
        if self.state.get_player(player_id).eliminated:
            await self._advance_turn()
            return
        await self._auto_draw(player_id)
        if self.state.get_player(player_id).eliminated:
            await self._advance_turn()
            return
        await self._process_play_on_draw()
        if (
            self._pending is None
            and self._pending_resolution is None
            and self._pending_auto_play is None
            and self.state.phase == "playing"
            and self.state.active_player().eliminated
        ):
            await self._advance_turn()
            return
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

        ``rules.hand_limit`` is enforced FIRST, before the ON_TURN_END hooks:
        when the active player's hand exceeds the limit, a synthetic discard
        plan pauses here and this method returns; its completion trims any
        remainder and re-enters ``_advance_turn``, whose limit check then
        passes — so the hooks fire exactly once per turn end, and cards a
        turn-end hook grants escape the limit until the player's next turn.
        """
        if not self.state.players:
            return
        if await self._maybe_enforce_hand_limit():
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

    # ── turn timer (rules.turn_timer) ──
    async def _arm_turn_timer(self, player_id: str) -> None:
        """Arm (or clear) the pausable turn clock for the turn now starting.

        ``rules.turn_timer`` is read once per turn here, so a mid-turn
        ``set_rule`` applies from the next turn. Rooms that never had a clock
        stay silent — the push only goes out when there is (or just was) one.
        """
        seconds = self.state.rules.turn_timer
        if seconds:
            self._turn_timer.start(seconds, player_id)
            await self._broadcast_turn_timer()
        elif self._turn_timer.running or self._turn_timer.paused:
            self._turn_timer.cancel()
            await self._broadcast_turn_timer()

    def _turn_timer_snapshot(self) -> dict | None:
        """Public turn-clock info for the snapshot and the turn_timer push, or
        None when no clock is live. ``deadline_epoch_ms`` is null while paused
        (the banked remainder is server-side only)."""
        timer = self._turn_timer
        if not timer.running and not timer.paused:
            return None
        return {
            "deadline_epoch_ms": timer.deadline_epoch_ms,
            "paused": timer.paused,
            "player_id": timer.player_id,
        }

    async def _broadcast_turn_timer(self) -> None:
        info = self._turn_timer_snapshot()
        await self.connections.broadcast(
            {
                "type": "turn_timer",
                "deadline_epoch_ms": info["deadline_epoch_ms"] if info else None,
                "paused": info["paused"] if info else False,
                "player_id": info["player_id"] if info else None,
            }
        )

    async def _pause_turn_timer(self) -> None:
        """Stop the turn clock while the room suspends (brewing, reaction
        window, interaction barrier) — the wait must not cost the player."""
        if self._turn_timer.pause():
            await self._broadcast_turn_timer()

    async def _maybe_resume_turn_timer(self) -> None:
        """Re-arm the paused clock once NO suspension remains.

        Called on every suspension's exit path and safe to over-call: it
        no-ops when the clock isn't paused, another suspension is still live,
        or a new turn already re-armed a fresh clock (start() clears the
        banked remainder). A rule lifted while the clock was paused cancels
        instead of resuming.
        """
        if not self._turn_timer.paused:
            return
        if (
            self._resolving_play is not None
            or self._pending is not None
            or self._pending_resolution is not None
            or self._pending_admin is not None
        ):
            return
        if self.state.rules.turn_timer is None:
            self._turn_timer.cancel()
        else:
            self._turn_timer.resume()
        await self._broadcast_turn_timer()

    async def _turn_timer_expired(self, generation: int) -> None:
        """Force the end-turn path when the active player's clock runs out.

        Same lock discipline as ``_reaction_timeout``: whoever wins the lock
        acts, and a stale expiry sees a bumped generation and no-ops. The
        remaining checks are belt-and-suspenders — any suspension pauses the
        clock (bumping the generation) and any turn change re-arms it — so an
        expiry can never end someone else's turn or fire into a suspended
        room.
        """
        async with self._lock:
            timer = self._turn_timer
            if generation != timer.generation:
                return
            player_id = timer.player_id
            timer.finish()
            await self._broadcast_turn_timer()
            if (
                self.state.phase != "playing"
                or not self.state.players
                or player_id is None
                or not self._is_active_player(player_id)
                or self.state.rules.turn_timer is None
                or self._resolving_play is not None
                or self._pending is not None
                or self._pending_resolution is not None
                or self._pending_admin is not None
            ):
                return
            await self._log_and_broadcast(f"{self._name(player_id)} ran out of time — the turn ends")
            await self._advance_turn()
            self._notify_change()

    # ── hand-limit enforcement (rules.hand_limit) ──
    async def _maybe_enforce_hand_limit(self) -> bool:
        """Enforce ``rules.hand_limit`` at end of turn via a synthetic plan.

        When the active player's hand exceeds the limit, run a one-interaction
        ResolutionPlan (a from_hand card_pick for exactly the excess, then a
        snippet destroying the picks) through the ordinary
        ``_execute_plan``/``_pause_resolution`` machinery. Returns True when
        the turn advance is now suspended behind that interaction — completion
        (or timeout, which discards from the hand tail) re-enters
        ``_advance_turn`` via ``_finish_hand_limit``. Eliminated players are
        exempt (their hand was already discarded; a stale over-limit hand must
        not wedge the advance). Enforcement must never brick the turn: if the
        pause cannot be set up, the tail is trimmed synchronously instead.
        """
        limit = self.state.rules.hand_limit
        if limit is None:
            return False
        active = self.state.active_player()
        if active.eliminated:
            return False
        excess = len(active.hand) - limit
        if excess <= 0:
            return False
        plan = self._hand_limit_plan(limit, min(excess, 200))
        ctx = HookContext(event=GameEvent.ON_TURN_END, actor_id=active.id)
        card = {"id": "", "title": f"Hand limit ({limit})"}
        try:
            self.state = await self._execute_plan(self.state, plan, ctx, card, working_state=self.state)
        except PlanPaused as paused:
            try:
                await self._pause_resolution(
                    paused,
                    plan=plan,
                    ctx=ctx,
                    card=card,
                    correlation_id=str(uuid.uuid4()),
                    before_scores={p.id: p.score for p in self.state.players},
                    deck_count_before=len(self.state.deck),
                    purpose="hand_limit",
                )
            except Exception as exc:
                logger.warning("hand limit interaction setup failed player=%s reason=%s", active.id, exc)
                self._trim_hand_to_limit(active.id)
                return False
            return True
        except Exception as exc:
            logger.warning("hand limit plan failed player=%s reason=%s", active.id, exc)
        self._trim_hand_to_limit(active.id)
        return False

    def _hand_limit_plan(self, limit: int, excess: int) -> ResolutionPlan:
        """The synthetic hand-limit ResolutionPlan: pick exactly ``excess``
        cards from your own hand, then destroy the picks. The snippet accepts
        both pick shapes (a bare id when excess == 1, else a list)."""
        noun = "card" if excess == 1 else f"{excess} cards"
        request = CardPickInteraction(
            prompt=f"Hand limit is {limit} — choose {noun} to discard",
            audience="active",
            from_hand=True,
            min_picks=excess,
            max_picks=excess,
            timeout_seconds=HAND_LIMIT_TIMEOUT_SECONDS,
        )
        code = (
            "def apply(state, ctx):\n"
            f"    picks = ctx['interactions']['{HAND_LIMIT_RESULT_KEY}'].get(ctx['actor_id'])\n"
            "    if isinstance(picks, str):\n"
            "        picks = [picks]\n"
            "    for card_id in picks or []:\n"
            "        state.destroy_card(card_id=card_id)\n"
        )
        return ResolutionPlan(
            steps=[
                InteractionStep(result_key=HAND_LIMIT_RESULT_KEY, request=request),
                SnippetStep(code=code, explanation="Destroy the cards picked for the hand limit."),
            ]
        )

    def _hand_limit_default_picks(self, pending: PendingResolution, player_id: str) -> list[str]:
        """Timeout auto-discard: the hand TAIL stands in for unmade picks."""
        hand = self._from_hand_options(player_id)
        count = getattr(pending.request, "max_picks", 1)
        return hand[len(hand) - min(count, len(hand)) :]

    def _trim_hand_to_limit(self, player_id: str) -> None:
        """Deterministically discard ``player_id``'s hand tail down to
        ``rules.hand_limit`` — the no-input backstop (failed synthetic plan,
        snippet error, or leftover excess). Guarantees the ``_advance_turn``
        re-entry check passes, so enforcement can never loop."""
        limit = self.state.rules.hand_limit
        if limit is None:
            return
        try:
            hand = self.state.get_player(player_id).hand
        except KeyError:
            return
        excess = len(hand) - limit
        if excess <= 0:
            return
        ops: list[Op] = [DestroyCardOp(card_id=cid) for cid in hand[-excess:]]
        ctx = HookContext(event=GameEvent.ON_TURN_END, actor_id=player_id)
        self.state = apply_effect(self.state, EffectProgram(ops=ops), ctx, bus=self._hook_bus())

    async def _finish_hand_limit(self, pending: PendingResolution) -> None:
        """Tail of the synthetic hand-limit resolution, success or failure:
        trim any remaining excess off the hand tail, log, and resume the turn
        advance the enforcement pause interrupted. Not a play — no zone move,
        no mechanical status, no play-allowance accounting."""
        limit = self.state.rules.hand_limit
        self._trim_hand_to_limit(pending.actor_id)
        await self._log_and_broadcast(f"{self._name(pending.actor_id)} discarded down to the hand limit ({limit})")
        await self._advance_turn()

    def _hook_bus(self) -> EventBus:
        fingerprint = tuple(h.id for h in self.state.hooks)
        if self._hook_registry is None or fingerprint != self._hook_fingerprint:
            self._hook_registry = build_registry(self.state)
            self._hook_fingerprint = fingerprint
        return EventBus(self._hook_registry, max_hooks=MAX_HOOKS_PER_EVENT)

    async def _emit_hooks(
        self,
        event: GameEvent,
        actor_id: str,
        *,
        card_id: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        """Fire registered hooks for ``event`` (off-loop: each fire is a sandbox
        subprocess) and adopt the resulting state. No-op when nothing subscribes.

        Hook-snippet failures are drained via ``collect_hook_errors`` and reported
        to the triage agent; ``correlation_id`` ties a report to the triggering play
        when the caller has one, else the failing hook's own card id stands in.
        """
        bus = self._hook_bus()
        if not self._hook_registry.hooks_for_event(str(event)):
            return
        ctx = HookContext(event=event, actor_id=actor_id, card_id=card_id)
        with collect_hook_errors() as errors, collect_hand_reveals() as reveals:
            self.state = await asyncio.to_thread(bus.emit, event, self.state, ctx)
        await self._push_hand_reveals(reveals)
        await self._push_dice_rolls()
        for err in errors:
            card = self.state.cards.get(err["card_id"]) or {"id": err["card_id"], "title": err["card_id"]}
            self._report_failure_for_triage(
                "hook_failure", card, correlation_id or err["card_id"], exc=RuntimeError(err["error"])
            )

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
                logger.warning(
                    "validation hook failed card_id=%s source=%s reason=%s", card_id, spec.source_card_id, exc
                )
                self._report_failure_for_triage("hook_failure", card, card_id, exc=exc)
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

    def _card_title(self, card, default: str = "a card") -> str:
        """A card's display title (falls back to ``default``)."""
        if isinstance(card, dict):
            return card.get("title") or default
        return getattr(card, "title", None) or default

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

    async def _end_game(self, *, emit_hooks: bool = True) -> None:
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
        if self._turn_timer.running or self._turn_timer.paused:
            self._turn_timer.cancel()
            await self._broadcast_turn_timer()
        if emit_hooks:
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
        if status == "fallback":
            creator_id = card.get("creator_id")
            if creator_id and any(p.id == creator_id for p in self.state.players):
                self.state = append_history_event(
                    self.state,
                    "card_fallback",
                    actor_id=creator_id,
                    target_player_ids=[creator_id],
                    card_id=card_id,
                )
        self.state = self.state.model_copy(update={"cards": {**self.state.cards, card_id: updated}})
        self._notify_change()

    def _consolation_author(self, card):
        """The seated author a consolation boon would go to, or None when the
        fallback stays a bare note (seed card, departed author, or
        consolation_point_enabled off). Single gate shared by _consolation_ops
        and _uninterpretable_reason so the promise and the award can't drift."""
        from config import get_settings

        if not get_settings().consolation_point_enabled:
            return None
        creator_id = card.get("creator_id") if isinstance(card, dict) else getattr(card, "creator_id", None)
        if not creator_id:
            return None
        return next((p for p in self.state.players if p.id == creator_id), None)

    def _uninterpretable_reason(self, card) -> str:
        if self._consolation_author(card) is None:
            return "The arbiter couldn't build this one."
        return "The arbiter couldn't build this one - the author gets a consolation boon for trying."

    def _consolation_ops(self, card, card_id: str) -> list[Op]:
        """Ops for a card that couldn't be made to work: a visible note plus a
        consolation boon to the AUTHOR "for trying" (authored cards only, author
        still seated, consolation_point_enabled on).

        The boon escalates with the author's card_fallback history count —
        every call site records the current failure (via
        _set_card_mechanical_status) BEFORE building these ops, so the count
        read here already includes it. Below struggling_author_threshold (or
        with escalation disabled, threshold == 0) it's a flat
        consolation_points award; at or past the threshold it rotates through
        +2 points, draw 3 cards, and a one-shot score double.
        """
        from config import get_settings

        title = self._card_title(card)
        bare = [CustomNoteOp(note=f"Played {title} (no mechanical effect)")]
        settings = get_settings()
        author = self._consolation_author(card)
        if author is None:
            return bare
        creator_id = author.id
        n = fallback_counts(self.state).get(creator_id, 0)
        threshold = settings.struggling_author_threshold
        boon: Op
        if threshold <= 0 or n < threshold:
            amount = settings.consolation_points
            boon = AddPointsOp(target=f"id:{creator_id}", amount=amount)
            boon_text = f"+{amount} point{'' if amount == 1 else 's'}"
        else:
            rung = (n - threshold) % 3
            if rung == 1:
                boon = DrawCardsOp(target=f"id:{creator_id}", amount=3)
                boon_text = "3 cards"
            elif rung == 2 and author.score > 0:
                boon = SetPointsOp(target=f"id:{creator_id}", amount=author.score * 2)
                boon_text = "score doubled"
            else:
                boon = AddPointsOp(target=f"id:{creator_id}", amount=2)
                boon_text = "+2 points"
        note = CustomNoteOp(note=f"Played {title} (no mechanical effect - {boon_text} to {author.name} for trying)")
        return [note, boon]

    def _report_failure_for_triage(
        self,
        kind: str,
        card,
        correlation_id: str,
        *,
        exc: Exception | None = None,
        verdict: str | None = None,
        comment: str | None = None,
    ) -> None:
        """Queue a triage-agent report for one failed effect execution.

        Best-effort by contract: never raises and never blocks the play path —
        the report is scheduled fire-and-forget (agent.triage) and
        any error here is swallowed at debug level. Deduped per (card_id, kind)
        so a card that keeps hitting the same failure mode reports once.
        """
        try:
            from config import get_settings

            settings = get_settings()
            if not settings.triage_agent_enabled:
                return
            card_id = (card.get("id") if isinstance(card, dict) else getattr(card, "id", None)) or correlation_id
            key = (card_id, kind)
            if settings.triage_agent_dedupe and key in self._reported_failures:
                return
            self._reported_failures.add(key)
            description = card.get("description") if isinstance(card, dict) else getattr(card, "description", None)
            creator_id = card.get("creator_id") if isinstance(card, dict) else getattr(card, "creator_id", None)
            try:
                from agent.tools.read_game_state import _summarize_state

                state_summary = _summarize_state(self.state, None, creator_id, card_id)
            except Exception:
                state_summary = ""
            try:
                from engine.history import draw_totals, public_history

                history_summary = f"{public_history(self.state, limit=20)}\ndraw totals: {draw_totals(self.state)}"
            except Exception:
                history_summary = ""
            from agent.triage import CardFailure, schedule_triage

            payload = CardFailure(
                kind=kind,
                card_title=self._card_title(card),
                card_description=description or "",
                card_id=card_id,
                correlation_id=correlation_id,
                verdict=verdict,
                comment=comment,
                exception=(
                    self._public_mechanical_reason(exc, fallback="effect execution failed") if exc is not None else None
                ),
                state_summary=state_summary,
                history_summary=history_summary,
                run_metrics=self._last_run_metrics.pop(card_id, None),
                langsmith=(
                    {"project": settings.langsmith_project, "correlation_id": correlation_id}
                    if settings.langsmith_tracing
                    else None
                ),
            )
            schedule_triage(payload)
        except Exception:
            logger.debug("effect-failure report skipped (kind=%s)", kind, exc_info=True)

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

        Schema v2 (data/eval/CANONICAL_SPEC.md): placement "center" = a shared
        rule/reminder/object; "player" = an owned or attached card in front of
        the affected player; "discard" = no continuing physical identity. Legacy v1 canonicals
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
        if placement == "player":
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
            if isinstance(card, dict):
                await self._log_agent_comment(card_id, str(card.get("agent_comment") or ""))
            return compiled

        from agent.contract import InterpretResult
        from agent.runtime import run_agent
        from config import get_settings

        settings = get_settings()
        # Instrumentation is attached only when the triage agent is on: the
        # callback and metrics bookkeeping exist solely to feed failure triage,
        # so the production-default path pays nothing.
        callback = None
        agent_config: dict | None = None
        if settings.triage_agent_enabled:
            from evals.instrumentation import UsageCallback

            callback = UsageCallback()
            agent_config = {"callbacks": [callback]}
            if settings.langsmith_tracing:
                agent_config["metadata"] = {
                    "card_id": card_id,
                    "correlation_id": correlation_id,
                    "kind_hint": "interpretation",
                }
                agent_config["run_name"] = f"interpret:{card_id}"

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
                config=agent_config,
            )
        except Exception:
            logger.exception("run_agent failed for %s; using deterministic fallback", card_id)
            result = InterpretResult(verdict="invalid", comment="", persona_action="none")
        finally:
            # In finally so a failed interpretation still records its metrics
            # for the eval-agent report.
            if callback is not None:
                self._last_run_metrics[card_id] = asdict(callback.snapshot())

        await self.connections.broadcast(
            {
                "type": "card_interpreted",
                "card_id": card_id,
                "program": str(result.program) if result.program is not None else None,
                "snippet": getattr(result.snippet, "code", None),
                "verdict": result.verdict,
                "comment": result.comment,
                "mechanical_status": "pending" if result.verdict == "ok" else "fallback",
                "mechanical_reason": (None if result.verdict == "ok" else self._uninterpretable_reason(card)),
                "correlation_id": correlation_id,
            }
        )

        await self._log_agent_comment(card_id, result.comment)

        canonical = self._canonicalize_interpretation(result, title=title, description=description)
        if (
            canonical
            and isinstance(self.state.cards.get(card_id), dict)
            and not self.state.cards[card_id].get("canonical")
        ):
            existing = self.state.cards[card_id]
            merged_card = {**existing, **canonical, "verdict": result.verdict}
            if "attributes" in canonical:
                merged_card["attributes"] = {**(existing.get("attributes") or {}), **canonical["attributes"]}
            self.state = self.state.model_copy(update={"cards": {**self.state.cards, card_id: merged_card}})

        plan = result.to_plan()
        if result.verdict == "ok" and plan.steps:
            return plan

        self._set_card_mechanical_status(
            card_id,
            "fallback",
            correlation_id,
            self._uninterpretable_reason(card),
        )
        self._report_failure_for_triage(
            "no_op" if result.verdict == "ok" else "invalid_verdict",
            card,
            correlation_id,
            verdict=result.verdict,
            comment=result.comment,
        )
        return ResolutionPlan(steps=[OpsStep(ops=self._consolation_ops(card, card_id))])

    @staticmethod
    def _infer_interpretation_placement(result, *, title: str = "", description: str = "") -> str:
        """Infer a safe physical zone when an otherwise-valid result omitted one."""
        operations = result.to_plan().operations()
        names = {getattr(op, "op", "") for op in operations}
        if names & {
            "set_rule",
            "change_draw_count",
            "set_win_condition",
            "reverse_order",
            "scramble_order",
        }:
            return "center"
        for op in operations:
            if getattr(op, "op", "") == "register_hook":
                return "center" if getattr(op, "scope", "center") == "center" else "player"
        for op in operations:
            if getattr(op, "op", "") == "set_condition":
                target = str(getattr(op, "target", ""))
                return "center" if target in {"all", "all_others"} else "player"
            if getattr(op, "op", "") == "reveal_hand" and getattr(op, "persistent", False):
                target = str(getattr(op, "target", ""))
                return "center" if target in {"all", "all_others"} else "player"

        text = f"{title}\n{description}".lower()
        if re.search(r"\bnew rule\b|\bhouse rule\b|\beveryone\b.*\b(?:now|must|cannot|can't)\b", text):
            return "center"
        if re.search(
            r"\b(cat|dog|pet|puppy|kitten|companion|familiar|owned|owner|yours|your item|your hat)\b",
            text,
        ):
            return "player"
        return "discard"

    def _canonicalize_interpretation(self, result, *, title: str = "", description: str = "") -> dict:
        """Build the structured ``canonical`` payload for an interpreted card.

        Programs serialize their live ops; a triggered snippet becomes a
        register_hook authoring op (single pipeline); an immediate snippet is
        carried as canonical["sandbox"] for the play path. A snippet the agent
        marks trigger="on_reaction" makes the card a REACTION: its canonical
        records the trigger (so the room recognises it) and its code runs when
        the card is played into a reaction window. Cards with neither
        contribute nothing (fall back to the LLM next time).

        A plan that tags THIS card with the ``play_on_draw`` attribute
        (set_card_attribute card_target="this") is likewise canonicalized: the
        attribute is persisted onto the card immediately (under an
        ``attributes`` key merged by the caller), so the card auto-plays on
        future draws even if this play never executes (countered/failed).

        The agent's ``placement``/``venue`` are recorded so
        ``_play_destination`` can zone the card. Missing placement on an
        otherwise-successful legacy result is inferred and persisted; failed
        interpretations always discard. No ``timing`` key is written.
        """
        plan = result.to_plan()
        canonical: dict = {}
        snippet = getattr(result, "snippet", None)
        trigger = getattr(result, "trigger", None) or (
            getattr(snippet, "trigger", None) if snippet is not None else None
        )
        if trigger is None and any(isinstance(op, CounterPlayOp) for op in plan.operations()):
            trigger = str(GameEvent.ON_REACTION)
        if trigger is not None:
            canonical["trigger"] = trigger
        placement = getattr(result, "placement", None)
        if result.verdict != "ok":
            placement = "discard"
        elif placement is None:
            placement = self._infer_interpretation_placement(result, title=title, description=description)
            logger.warning("successful interpretation omitted placement; inferred %s for %r", placement, title)
        canonical["placement"] = placement
        venue = getattr(result, "venue", None)
        if venue is not None:
            canonical["venue"] = venue
        if not plan.steps:
            return {"canonical": canonical} if canonical else {}
        canonical["steps"] = [step.model_dump() for step in plan.steps]
        ops = [op.model_dump() for step in plan.steps if isinstance(step, OpsStep) for op in step.ops]
        snippets = [step for step in plan.steps if isinstance(step, SnippetStep)]
        if ops:
            canonical["ops"] = ops
        if len(snippets) == 1 and isinstance(plan.steps[-1], SnippetStep):
            canonical["sandbox"] = snippets[0].code
        merged: dict = {"canonical": canonical}
        static_attributes = hoist_static_attributes(plan.operations())
        if static_attributes:
            merged["attributes"] = static_attributes
        return merged

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
        # Reveals are pushed in the finally so a plan that pauses on an
        # interaction barrier (or fails a later step) still delivers the
        # one-shot reveals its earlier steps produced.
        with collect_hand_reveals() as reveals:
            try:
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
            finally:
                await self._push_hand_reveals(reveals)
        return working

    async def _push_hand_reveals(self, reveals: list[dict]) -> None:
        """Deliver one-shot hand reveals to exactly their resolved audience.

        Each entry (collected by ``engine.reducers.collect_hand_reveals``) is a
        targeted ``hand_revealed`` push — modal, like the reaction window: not
        state, so it is lost on reconnect (acceptable by design). The card
        bodies ride the message because the audience's redacted snapshots never
        carry hidden hand content.
        """
        for entry in reveals:
            message = {
                "type": "hand_revealed",
                "player_id": entry["player_id"],
                "player_name": self._name(entry["player_id"]),
                "card_ids": list(entry["card_ids"]),
                "cards": dict(entry["cards"]),
            }
            for viewer_id in entry["viewer_ids"]:
                await self.connections.send(viewer_id, message)

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
        # Resolution may have merged a fresh canonical (placement/venue) onto
        # the persisted card; the zone move downstream must see it.
        card = persisted
        chosen_player_id = getattr(msg, "chosen_player_id", None)
        chosen_card_id = getattr(msg, "chosen_card_id", None)
        valid_player_ids = {p.id for p in self.state.players}
        needs_player_choice, needs_card_choice = plan_choice_needs(plan)

        if needs_player_choice and chosen_player_id is None:
            await self.connections.send(
                player_id,
                self._prompt_choice_msg(
                    card_id,
                    f"Choose a target player for {title}",
                    [{"player_id": p.id, "name": p.name} for p in self.state.players],
                    chosen_card_id=chosen_card_id,
                ),
            )
            return
        if chosen_player_id is not None and chosen_player_id not in valid_player_ids:
            await self.connections.send(
                player_id,
                {"type": "error", "message": f"Invalid target player: {chosen_player_id}"},
            )
            return
        if needs_card_choice:
            valid_card_ids = chosen_card_candidates(
                self.state, plan, player_id, card_id, chosen_player_id=chosen_player_id
            )
            if not valid_card_ids:
                await self.connections.send(
                    player_id,
                    {"type": "error", "message": f"There is no eligible target card for {title}"},
                )
                return
            if chosen_card_id is None:
                await self.connections.send(
                    player_id,
                    self._prompt_choice_msg(
                        card_id,
                        f"Choose a target card for {title}",
                        self._card_choice_payload(valid_card_ids),
                        cards=self._card_choice_snapshots(valid_card_ids),
                        chosen_player_id=chosen_player_id,
                    ),
                )
                return
            if chosen_card_id not in valid_card_ids:
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
        count_as_play: bool = True,
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
        fallback). ``count_as_play=False`` marks a play_on_draw auto-play: same
        commit semantics, but it never consumes the play allowance or advances
        the turn (see _after_play_effects).
        """
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
                        purpose="play" if count_as_play else "auto_play",
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
                    self._report_failure_for_triage("interaction_setup", card, correlation_id, exc=exc)
                    destination = self._play_destination(card)
                    self.state = self.state.move_card(
                        card_id, "hand", destination, from_player_id=player_id, to_player_id=player_id
                    )
                    fallback = EffectProgram(ops=self._consolation_ops(card, card_id))
                    self.state = apply_effect(self.state, fallback, ctx, bus=self._hook_bus())
                    await self._log_and_broadcast(self._describe_play(player_id, card, before))
                    game_ending = self._end_now() or win_condition_met(self.state)
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
                self._report_failure_for_triage("sandbox_failure", card, correlation_id, exc=exc)
                destination = self._play_destination(card)
                self.state = self.state.move_card(
                    card_id,
                    "hand",
                    destination,
                    from_player_id=player_id,
                    to_player_id=player_id,
                )
                fallback = EffectProgram(ops=self._consolation_ops(card, card_id))
                self.state = apply_effect(self.state, fallback, ctx, bus=self._hook_bus())
                await self._log_and_broadcast(self._describe_play(player_id, card, before))
                game_ending = self._end_now() or win_condition_met(self.state)
            else:
                current = self.state.cards.get(card_id)
                if not isinstance(current, dict) or current.get("mechanical_status") != "fallback":
                    self._set_card_mechanical_status(card_id, "applied", correlation_id)
                await self._log_and_broadcast(self._describe_play(player_id, card, before))
                await self._emit_hooks(GameEvent.ON_PLAY, player_id, card_id=card_id, correlation_id=correlation_id)
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
            count_as_play=count_as_play,
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
        count_as_play: bool = True,
    ) -> None:
        """The single post-play accounting tail, shared by direct plays
        (_finish_play) and resumed interaction plays (_complete_interaction_play):
        dice pushes, history, deck-exhaustion latch, play allowance,
        play_on_draw scan, broadcast, end/advance. Runs exactly once per
        original play regardless of outcome.

        ``target_player_ids`` records who (beyond the actor) this play was
        aimed at, when known — e.g. a card played to a chosen player, or
        an interaction's resolved audience. Defaults to empty (no known
        target) rather than the actor, since actor_id already covers that.

        ``count_as_play=False`` (a play_on_draw auto-play) skips the play
        allowance AND the turn decision — the turn belongs to whoever was
        already playing it. When a counted play's auto-play chain suspends
        (reaction window / interaction barrier), the turn decision is deferred
        via ``_advance_after_auto_play`` and the chain's completing tail runs
        it here instead.
        """
        await self._push_dice_rolls()
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

        if count_as_play:
            self._plays_this_turn += 1
        if not game_ending:
            # Mid-effect draws (draw_cards inside the plan) may have landed
            # play_on_draw cards in hands — they resolve before the turn moves
            # on. Skipped when this play already ends the game.
            await self._process_play_on_draw()
        if self.state.phase != "playing":
            # A chained auto-play already ended the game (its own tail ran
            # _end_game); nothing left to account for.
            return
        game_ending = game_ending or self._end_now() or win_condition_met(self.state)
        await self._broadcast_state()
        if self._pending is not None or self._pending_resolution is not None or self._pending_auto_play is not None:
            # The scan suspended the room mid-chain (reaction window,
            # interaction barrier, or an auto-play awaiting its owner's
            # prompt_choice answer). Defer this counted play's turn decision
            # to the chain's completing (count_as_play=False) tail; an
            # uncounted tail has nothing of its own to defer.
            if count_as_play:
                self._advance_after_auto_play = True
            return
        if not count_as_play:
            if self._advance_after_auto_play:
                self._advance_after_auto_play = False
                await self._turn_decision(game_ending)
            elif game_ending:
                await self._end_game()
            elif self.state.active_player().eliminated:
                await self._advance_turn()
            return
        await self._turn_decision(game_ending)

    async def _turn_decision(self, game_ending: bool) -> None:
        """End/advance/continue after a counted play (the classic play tail)."""
        if game_ending:
            # end_game / a live win condition ends the game NOW, deck or no deck —
            # unlike deck exhaustion, which lets the drawer finish their turn.
            await self._end_game()
        elif self.state.active_player().eliminated:
            # The play eliminated the player whose turn it is — their turn cannot
            # continue, so advance regardless of remaining play allowance.
            await self._advance_turn()
        elif self._plays_this_turn < self.state.rules.play:
            # rules.play > 1: the turn continues until the play allowance is
            # spent (or the player passes).
            await self._broadcast_state()
        else:
            await self._advance_turn()

    def _history_seq(self) -> int:
        """The last recorded history sequence (0 when history is empty)."""
        return self.state.history_events[-1].sequence if self.state.history_events else 0

    async def _push_dice_rolls(self) -> None:
        """Broadcast one dice_roll push per roll not yet pushed, then advance
        the ``_dice_seq_pushed`` watermark to the history tip.

        The history event is the reconnect-safe record (it rides every state
        snapshot); this push is the immediacy signal that drives the client's
        roll animation — the brewing/reaction_window split. The watermark makes
        the push idempotent, so every state-mutating tail (play, reaction,
        lifecycle hooks) can call this without double-pushing.
        """
        watermark = self._dice_seq_pushed
        self._dice_seq_pushed = self._history_seq()
        for event in self.state.history_events:
            if event.sequence <= watermark or event.kind != "dice_roll":
                continue
            data = event.data or {}
            await self.connections.broadcast(
                {
                    "type": "dice_roll",
                    "actor_id": event.actor_id or "",
                    "sides": data.get("sides", 0),
                    "values": list(data.get("values", [])),
                    "total": data.get("total", 0),
                    "card_id": event.card_id,
                }
            )

    # ── play_on_draw auto-plays ──
    def _card_choice_payload(self, card_ids: list[str]) -> list[dict[str, str]]:
        """prompt_choice card entries, in candidate order (see board.rooms.choices)."""
        return [
            {
                "card_id": cid,
                "name": self._card_title(self.state.cards.get(cid), default=cid),
            }
            for cid in card_ids
        ]

    def _card_choice_snapshots(self, card_ids: list[str], *, cards: dict | None = None) -> dict[str, dict]:
        """Full card snapshots for exactly the offered candidates.

        Rides the targeted card prompt/interaction because the chooser's
        redacted state snapshot never carries another player's hidden hand
        content (same rationale as HandRevealedMsg.cards). Must only ever be
        sent to the chooser — never broadcast, never persisted. ``cards``
        overrides the registry snapshots are read from (interactions read the
        paused resolution's working state).
        """
        source = self.state.cards if cards is None else cards
        snapshots: dict[str, dict] = {}
        for cid in card_ids:
            card = source.get(cid)
            if isinstance(card, dict):
                snapshots[cid] = dict(card)
            elif card is not None:
                snapshots[cid] = card.model_dump()
        return snapshots

    def _prompt_choice_msg(
        self,
        card_id: str,
        prompt: str,
        choices: list[dict],
        *,
        cards: dict[str, dict] | None = None,
        chosen_player_id: str | None = None,
        chosen_card_id: str | None = None,
        as_reaction: bool = False,
    ) -> dict:
        """One prompt_choice envelope carrying the accumulated two-step context
        (see PromptChoiceMsg) — the follow-up play must re-send every choice
        made so far, so each prompt echoes what is already decided."""
        return {
            "type": "prompt_choice",
            "card_id": card_id,
            "prompt": prompt,
            "choices": choices,
            "cards": cards or {},
            "chosen_player_id": chosen_player_id,
            "chosen_card_id": chosen_card_id,
            "as_reaction": as_reaction,
        }

    def _is_play_on_draw(self, card) -> bool:
        """True when the card carries the ``play_on_draw`` attribute — it never
        rests in a hand; the room plays it for its holder the moment it lands
        there (drawn, dealt, minted, or given). An ATTRIBUTE, not a hook event."""
        if not isinstance(card, dict):
            return False
        for bag_key in ("properties", "attributes"):
            bag = card.get(bag_key)
            if isinstance(bag, dict) and bag.get("play_on_draw"):
                return True
        return False

    def _play_on_draw_candidates(self) -> list[tuple[str, str]]:
        """(owner_id, card_id) pairs of unprocessed play_on_draw cards in hands.

        Excludes cards deferred this turn (recursion cap / veto / turned out to
        be a reaction), the card awaiting its owner's prompt_choice answer,
        blanks (nothing to auto-author), and eliminated players."""
        pending_prompt = self._pending_auto_play.card_id if self._pending_auto_play is not None else None
        candidates: list[tuple[str, str]] = []
        for player in self.state.players:
            if player.eliminated:
                continue
            for cid in player.hand:
                if cid in self._auto_play_deferred or cid == pending_prompt:
                    continue
                card = self.state.cards.get(cid)
                if not self._is_play_on_draw(card):
                    continue
                if self._is_blank(card) or self._is_reaction_card(card):
                    continue
                candidates.append((player.id, cid))
        return candidates

    async def _process_play_on_draw(self) -> None:
        """Auto-play every unprocessed play_on_draw card, newest state first.

        The Room choke-point scan (called after the turn-start auto-draw and
        from every play's accounting tail, so mid-effect draw_cards are
        covered). Each auto-play runs the normal resolve/execute path as its
        OWNER at no action cost. Stops when the room suspends (reaction
        window, interaction barrier, or an auto-play awaiting its owner's
        prompt_choice answer) — the suspension's completing tail re-enters
        here — and hard-caps at MAX_AUTO_PLAYS_PER_TURN per turn, deferring
        the rest to a later turn with a log line.
        """
        while (
            self.state.phase == "playing"
            and self._pending is None
            and self._pending_resolution is None
            and self._pending_auto_play is None
        ):
            candidates = self._play_on_draw_candidates()
            if not candidates:
                return
            if self._auto_plays_this_turn >= MAX_AUTO_PLAYS_PER_TURN:
                for owner_id, card_id in candidates:
                    self._auto_play_deferred.add(card_id)
                    card = self.state.cards.get(card_id, {})
                    await self._log_and_broadcast(
                        f"{self._name(owner_id)}'s {self._card_title(card)} wants to play itself, but the "
                        f"auto-play limit ({MAX_AUTO_PLAYS_PER_TURN} per turn) is reached — it stays in hand"
                    )
                return
            owner_id, card_id = candidates[0]
            self._auto_plays_this_turn += 1
            await self._auto_play_card(owner_id, card_id)

    async def _auto_play_card(self, owner_id: str, card_id: str) -> None:
        """Play one play_on_draw card on its owner's behalf, at no action cost.

        Mirrors _handle_play minus its gates and accounting: validation hooks
        may still veto (the card then waits in hand until a later turn),
        resolution prefers the compiled plan (create_card-minted cards carry
        ops — no LLM round-trip) and falls back to brewing, a plan needing a
        play-time choice prompts the OWNER (see PendingAutoPlay), and reaction
        windows still open for the play.
        """
        card = self.state.cards.get(card_id)
        if card is None:
            self._auto_play_deferred.add(card_id)
            return
        await self._log_and_broadcast(
            f"{self._name(owner_id)}'s {self._card_title(card)} plays itself the moment it arrives!"
        )
        veto = await self._check_play_veto(owner_id, card_id, card)
        if veto is not None:
            self._auto_play_deferred.add(card_id)
            await self._log_and_broadcast(
                f"[rule] {self._name(owner_id)}'s {self._card_title(card)} was rejected: {veto}"
            )
            return
        correlation_id = str(uuid.uuid4())
        self._set_card_mechanical_status(card_id, "pending", correlation_id)
        previous_resolving = self._resolving_play
        self._resolving_play = card_id
        await self._pause_turn_timer()
        try:
            plan = await self._resolve_plan(card_id, card, actor_id=owner_id, correlation_id=correlation_id)
        finally:
            self._resolving_play = previous_resolving
            await self._maybe_resume_turn_timer()
        card = self.state.cards.get(card_id, card)
        if self._is_reaction_card(card):
            # Canonicalized as a reaction after all: it waits in hand for a
            # reaction window like any other reaction card.
            self._auto_play_deferred.add(card_id)
            return
        needs_player_choice, needs_card_choice = plan_choice_needs(plan)
        if needs_player_choice or needs_card_choice:
            self._pending_auto_play = PendingAutoPlay(
                owner_id=owner_id, card_id=card_id, plan=plan, correlation_id=correlation_id
            )
            await self._send_auto_play_prompt()
            return
        await self._commit_auto_play(
            owner_id, card_id, card, plan, correlation_id, chosen_player_id=None, chosen_card_id=None
        )

    async def _send_auto_play_prompt(self) -> None:
        """prompt_choice the pending auto-play's owner for the next missing
        choice (player axis first, then card — the normal two-prompt order).

        A card axis with NO eligible candidates never falls back to a hand:
        the auto-play is abandoned as a logged safe no-op (card deferred in
        hand) and the suspended chain resumes."""
        pending = self._pending_auto_play
        if pending is None:
            return
        card = self.state.cards.get(pending.card_id, {})
        title = self._card_title(card)
        needs_player_choice, needs_card_choice = plan_choice_needs(pending.plan)
        if needs_player_choice and pending.chosen_player_id is None:
            await self.connections.send(
                pending.owner_id,
                self._prompt_choice_msg(
                    pending.card_id,
                    f"Choose a target player for {title}",
                    [{"player_id": p.id, "name": p.name} for p in self.state.players],
                    chosen_card_id=pending.chosen_card_id,
                ),
            )
            return
        if needs_card_choice and pending.chosen_card_id is None:
            valid_card_ids = chosen_card_candidates(
                self.state,
                pending.plan,
                pending.owner_id,
                pending.card_id,
                chosen_player_id=pending.chosen_player_id,
            )
            if not valid_card_ids:
                self._pending_auto_play = None
                self._auto_play_deferred.add(pending.card_id)
                await self._log_and_broadcast(
                    f"{self._name(pending.owner_id)}'s {title} has no eligible target card — it stays in hand"
                )
                await self._finish_auto_play_chain()
                return
            await self.connections.send(
                pending.owner_id,
                self._prompt_choice_msg(
                    pending.card_id,
                    f"Choose a target card for {title}",
                    self._card_choice_payload(valid_card_ids),
                    cards=self._card_choice_snapshots(valid_card_ids),
                    chosen_player_id=pending.chosen_player_id,
                ),
            )

    async def _resume_auto_play(self, player_id: str, msg) -> None:
        """The owner's prompt_choice follow-up for a suspended auto-play
        (routed from _dispatch by owner + card_id, bypassing the turn gates)."""
        pending = self._pending_auto_play
        if pending is None:
            return
        try:
            hand = self.state.get_player(player_id).hand
        except KeyError:
            hand = []
        card = self.state.cards.get(pending.card_id)
        if card is None or pending.card_id not in hand:
            self._pending_auto_play = None
            await self.connections.send(player_id, {"type": "error", "message": "That card is no longer in your hand"})
            await self._finish_auto_play_chain()
            return
        chosen_player_id = getattr(msg, "chosen_player_id", None) or pending.chosen_player_id
        chosen_card_id = getattr(msg, "chosen_card_id", None) or pending.chosen_card_id
        if chosen_player_id is not None and chosen_player_id not in {p.id for p in self.state.players}:
            await self.connections.send(
                player_id, {"type": "error", "message": f"Invalid target player: {chosen_player_id}"}
            )
            return
        if chosen_card_id is not None:
            valid_card_ids = chosen_card_candidates(
                self.state, pending.plan, pending.owner_id, pending.card_id, chosen_player_id=chosen_player_id
            )
            if chosen_card_id not in valid_card_ids:
                await self.connections.send(
                    player_id, {"type": "error", "message": f"Invalid target card: {chosen_card_id}"}
                )
                return
        pending.chosen_player_id = chosen_player_id
        pending.chosen_card_id = chosen_card_id
        needs_player_choice, needs_card_choice = plan_choice_needs(pending.plan)
        if (needs_player_choice and chosen_player_id is None) or (needs_card_choice and chosen_card_id is None):
            await self._send_auto_play_prompt()
            return
        self._pending_auto_play = None
        await self._commit_auto_play(
            player_id,
            pending.card_id,
            card,
            pending.plan,
            pending.correlation_id,
            chosen_player_id=chosen_player_id,
            chosen_card_id=chosen_card_id,
        )

    async def _finish_auto_play_chain(self) -> None:
        """Resume a suspended auto-play chain whose pending prompt died without
        a completing play tail (its card left the owner's hand). Scans for the
        candidates queued behind it, then — unless the room re-suspended —
        honours the deferred turn decision of the counted play that started
        the chain, so a spent turn can never strand on the dead prompt."""
        await self._process_play_on_draw()
        if self.state.phase != "playing":
            return
        if self._pending is not None or self._pending_resolution is not None or self._pending_auto_play is not None:
            return
        if self._advance_after_auto_play:
            self._advance_after_auto_play = False
            await self._turn_decision(self._end_now() or win_condition_met(self.state))
        elif self.state.active_player().eliminated:
            await self._advance_turn()

    async def _commit_auto_play(
        self,
        owner_id: str,
        card_id: str,
        card,
        plan: ResolutionPlan,
        correlation_id: str,
        *,
        chosen_player_id: str | None,
        chosen_card_id: str | None,
    ) -> None:
        ctx = HookContext(
            event=GameEvent.ON_PLAY,
            actor_id=owner_id,
            card_id=card_id,
            chosen_player_id=chosen_player_id,
            chosen_card_id=chosen_card_id,
        )
        if await self._maybe_open_reaction_window(owner_id, card_id, card, plan, ctx, count_as_play=False):
            return
        await self._finish_play(owner_id, card_id, card, plan, ctx, correlation_id=correlation_id, count_as_play=False)

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
            base = request.model_dump(mode="python")
            base["max_selections"] = min(request.max_selections, len(source))
            labels = {str(player_id): self._name(str(player_id)) for player_id in source}
            skeleton = ChoiceInteraction.model_validate(
                {**base, "options": [{"id": option_id, "label": label} for option_id, label in labels.items()]}
            )
            available = (
                MAX_INTERACTION_DESCRIPTOR_BYTES
                - len(skeleton.model_dump_json().encode())
                - _PREVIEW_SERIALIZATION_MARGIN
            )
            budget = min(MAX_OPTION_PAYLOAD_BYTES, available // max(len(source), 1))
            while True:
                try:
                    request = ChoiceInteraction.model_validate(
                        {
                            **base,
                            "options": [
                                {
                                    "id": str(player_id),
                                    "label": labels[str(player_id)],
                                    "payload": compact_drawing_preview(value, budget),
                                }
                                for player_id, value in source.items()
                            ],
                        }
                    )
                    break
                except ValidationError:
                    if budget <= _MIN_PREVIEW_BUDGET:
                        raise
                    budget //= 2
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
        purpose: str = "play",
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
            purpose=purpose,
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
        await self._pause_turn_timer()
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
        which mirrors this by validating against the responder's hand).

        Deck-top interactions (``card_order``, ``from_deck_top`` card_pick) get
        the actual top-N card ids filled in here — and only here.

        Every card_pick and card_order additionally carries full faces for
        exactly its offered ``card_ids`` so choosers always see complete cards.
        Hidden information (deck contents, hand contents) rides this targeted
        interaction_request only (whose recipients are exactly the resolved
        audience; see :meth:`_send_interaction_request`) and never the shared
        snapshot; the audience's redacted snapshot keeps just those registry
        entries while the interaction is pending (see board.rooms.redaction).
        """
        descriptor = request.model_dump(mode="json")
        if isinstance(request, CardPickInteraction) and request.from_hand:
            descriptor["card_ids"] = list(self._from_hand_options(player_id))
        deck_top_count = self._deck_top_count(request)
        if deck_top_count is not None:
            offered = self._deck_top_options(deck_top_count)
            if isinstance(request, CardPickInteraction):
                claimed = self._claimed_deck_top_picks(player_id)
                offered = [cid for cid in offered if cid not in claimed]
            descriptor["card_ids"] = list(offered)
        if deck_top_count is not None or isinstance(request, CardPickInteraction):
            descriptor["cards"] = self._card_choice_snapshots(
                list(descriptor.get("card_ids") or []),
                cards=self._interaction_state().cards,
            )
        return descriptor

    @staticmethod
    def _deck_top_count(request: InteractionDescriptor) -> int | None:
        """How many deck-top cards this interaction reveals to its audience (None if none)."""
        if isinstance(request, CardOrderInteraction):
            return request.count
        if isinstance(request, CardPickInteraction):
            return request.from_deck_top
        return None

    def _interaction_state(self) -> GameState:
        """The state interaction options are drawn from: the paused resolution's
        working_state when one is live (the played card has already left the
        actor's hand there), falling back to committed state."""
        if self._pending_resolution is not None:
            return self._pending_resolution.working_state
        return self.state

    def _deck_top_options(self, count: int) -> list[str]:
        """The top ``count`` deck card ids a deck-top interaction offers (top first)."""
        return list(self._interaction_state().deck[:count])

    def _claimed_deck_top_picks(self, player_id: str | None) -> set[str]:
        """Deck-top cards OTHER audience members already picked in the pending
        interaction — unavailable to ``player_id`` (see
        :meth:`_validate_interaction_response`)."""
        pending = self._pending_resolution
        if pending is None:
            return set()
        claimed: set[str] = set()
        for pid, payload in pending.responses.items():
            if pid != player_id and isinstance(payload, CardPickResponse):
                claimed.update(payload.picks)
        return claimed

    def _from_hand_options(self, player_id: str) -> list[str]:
        """The hand a from_hand card_pick offers ``player_id`` (working state
        while paused, so the actor is never offered the card they are mid-play)."""
        try:
            return list(self._interaction_state().get_player(player_id).hand)
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
        """Resume persisted timers without waiting for reconnect.

        Interaction deadlines are re-scheduled from their persisted absolute
        deadline; the turn clock is transient (its remainder is never
        persisted), so a room restored mid-turn re-arms a FRESH full clock for
        the active player — generous, never unfair.
        """
        if self._pending_resolution is not None:
            self._schedule_interaction_timeout()
        if self._pending_admin is not None:
            self._schedule_admin_timeout()
        if (
            self.state.phase == "playing"
            and self.state.players
            and self.state.rules.turn_timer
            and not self._turn_timer.running
            and not self._turn_timer.paused
            and self._pending_resolution is None
            and self._pending is None
            and self._pending_admin is None
        ):
            self._turn_timer.start(self.state.rules.turn_timer, self.state.active_player().id)

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
            # from_hand / from_deck_top picks are validated against the option
            # set _send_interaction_request presented (the responder's hand /
            # the deck top), not the static card_ids (empty for those picks).
            if request.from_hand:
                selectable = set(self._from_hand_options(player_id)) if player_id is not None else set()
            elif request.from_deck_top is not None:
                # The deck top is one SHARED resource (unlike from_hand's
                # disjoint hands): with a multi-player audience, a card another
                # responder already claimed cannot be claimed again.
                claimed = self._claimed_deck_top_picks(player_id)
                if set(payload.picks) & claimed:
                    raise ValueError("card was already taken by another player")
                selectable = set(self._deck_top_options(request.from_deck_top)) - claimed
            else:
                selectable = set(request.card_ids)
            picks = payload.picks
            if not set(picks) <= selectable:
                raise ValueError("card is not selectable")
            # Never demand more than the responder can offer (a 2-card hand can't
            # satisfy "discard 3"); the floor clamps to the options available.
            low = min(request.min_picks, len(selectable))
            if not low <= len(picks) <= request.max_picks:
                raise ValueError("wrong number of cards picked")
            # Back-compat: a single-pick request returns a bare card id (what
            # existing snippets read); a multi-pick request returns the list.
            if request.max_picks == 1:
                return picks[0] if picks else None
            return picks
        if isinstance(request, CardOrderInteraction) and isinstance(payload, CardOrderResponse):
            # The response must be a full permutation of the offered top-N:
            # every offered card accounted for exactly once (uniqueness across
            # the split is model-enforced), and no foreign ids smuggled in.
            offered = self._deck_top_options(request.count)
            returned = [*payload.order, *payload.to_bottom]
            if sorted(returned) != sorted(offered):
                raise ValueError("card_order response must be a permutation of the offered cards")
            return {"order": list(payload.order), "to_bottom": list(payload.to_bottom)}
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
        if isinstance(pending.request, CardPickInteraction) and pending.request.from_deck_top is not None:
            # A recorded deck-top pick shrinks everyone else's options; resend
            # so no one is shown a card they can no longer claim.
            for member in pending.resolved_audience:
                await self._send_interaction_request(member)
        else:
            await self._send_interaction_request(player_id)
        await self._broadcast_interaction_progress()
        if len(pending.responses) >= len(pending.resolved_audience):
            await self._resume_pending_resolution(timed_out=False)

    def _default_interaction_value(self, request: InteractionDescriptor) -> object:
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
            # Mirror the pick shape: a single-pick request defaults to no card;
            # a multi-pick request defaults to the empty set.
            return None if request.max_picks == 1 else []
        if isinstance(request, CardOrderInteraction):
            # A silent scryer changes nothing: identity order, nothing bottomed.
            return {"order": self._deck_top_options(request.count), "to_bottom": []}
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
        if timed_out and not pending.responses and pending.purpose != "hand_limit":
            timeout_notice = "No one responded before the interaction timed out."
            await self._fail_pending_resolution(timeout_notice, notice=timeout_notice)
            return
        values: dict[str, object] = {}
        for player_id in pending.resolved_audience:
            payload = pending.responses.get(player_id)
            if payload is not None:
                values[player_id] = self._validate_interaction_response(pending.request, payload, player_id)
            elif pending.purpose == "hand_limit":
                # The hand-limit discard must always happen: an unresponsive
                # player's picks default to their hand tail, not to "no picks".
                values[player_id] = self._hand_limit_default_picks(pending, player_id)
            else:
                values[player_id] = self._default_interaction_value(pending.request)
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
                    purpose=pending.purpose,
                )
            except Exception as exc:
                self._report_failure_for_triage("interaction_resolve", pending.card, pending.correlation_id, exc=exc)
                await self._fail_pending_resolution(
                    self._public_mechanical_reason(exc, fallback="The next interaction could not be started safely."),
                    pending=pending,
                )
            return
        except Exception as exc:
            self._report_failure_for_triage("interaction_resolve", pending.card, pending.correlation_id, exc=exc)
            await self._fail_pending_resolution(
                self._public_mechanical_reason(exc, fallback="The interaction effect could not be applied safely."),
                pending=pending,
            )
            return
        await self._commit_pending_resolution(pending, completed)

    async def _fail_pending_resolution(
        self, reason: str, *, pending: PendingResolution | None = None, notice: str | None = None
    ) -> None:
        """Resolve a paused card into its no-effect fallback.

        ``reason`` is a dev/triage-facing detail: stored as the card's private
        mechanical_reason and logged, never broadcast — mechanical errors must
        not leak into the shared player log. ``notice`` is an optional
        player-facing line (e.g. a timeout explanation); when omitted, players
        just see the normal "played the card" description.
        """
        pending = pending or self._pending_resolution
        if pending is None:
            return
        self._pending_resolution = None
        if pending.purpose == "hand_limit":
            # Not a play: nothing to move or compensate. The tail trim in
            # _finish_hand_limit is the enforcement of last resort.
            logger.warning("hand limit interaction failed player=%s reason=%s", pending.actor_id, reason)
            await self._finish_hand_limit(pending)
            return
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
        logger.warning(
            "interaction resolve failed correlation_id=%s card_id=%s reason=%s",
            pending.correlation_id,
            pending.card_id,
            reason,
        )
        ctx = HookContext(event=GameEvent.ON_PLAY, actor_id=pending.actor_id, card_id=pending.card_id)
        self.state = apply_effect(
            self.state,
            EffectProgram(ops=self._consolation_ops(pending.card, pending.card_id)),
            ctx,
            bus=self._hook_bus(),
        )
        await self._log_and_broadcast(
            notice or self._describe_play(pending.actor_id, pending.card, pending.before_scores)
        )
        await self._complete_interaction_play(
            pending,
            game_ending=self._end_now() or win_condition_met(self.state),
        )
        await self._maybe_resume_turn_timer()

    async def _commit_pending_resolution(self, pending: PendingResolution, completed: GameState) -> None:
        self.state = completed
        if pending.purpose == "hand_limit":
            await self._finish_hand_limit(pending)
            return
        self._set_card_mechanical_status(pending.card_id, "applied", pending.correlation_id)
        await self._log_and_broadcast(self._describe_play(pending.actor_id, pending.card, pending.before_scores))
        await self._emit_hooks(
            GameEvent.ON_PLAY, pending.actor_id, card_id=pending.card_id, correlation_id=pending.correlation_id
        )
        await self._complete_interaction_play(
            pending,
            game_ending=self._end_now() or win_condition_met(self.state),
        )
        await self._maybe_resume_turn_timer()

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
            count_as_play=pending.purpose != "auto_play",
        )

    # ── reaction window ──
    async def _maybe_open_reaction_window(
        self, player_id: str, card_id: str, card, plan: ResolutionPlan, ctx: HookContext, *, count_as_play: bool = True
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
            count_as_play=count_as_play,
        )
        pending.timer = asyncio.create_task(self._reaction_timeout(window_id, REACTION_WINDOW_SECONDS))
        self._pending = pending
        await self._pause_turn_timer()
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
                count_as_play=pending.count_as_play,
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
                count_as_play=pending.count_as_play,
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
                count_as_play=pending.count_as_play,
            )
        else:
            await self._finish_play(
                pending.actor_id,
                pending.card_id,
                pending.card,
                pending.plan,
                ctx,
                correlation_id=correlation_id,
                count_as_play=pending.count_as_play,
            )
        await self._maybe_resume_turn_timer()

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
        needs_player_choice, needs_card_choice = plan_choice_needs(plan)
        if needs_player_choice and chosen_player_id is None:
            # Same suspend/resume as a normal play: the follow-up play message
            # re-enters here carrying as_reaction + the choice.
            await self.connections.send(
                player_id,
                self._prompt_choice_msg(
                    card_id,
                    f"Choose a target player for {self._card_title(card)}",
                    [{"player_id": p.id, "name": p.name} for p in self.state.players],
                    chosen_card_id=chosen_card_id,
                    as_reaction=True,
                ),
            )
            return
        if chosen_player_id is not None and chosen_player_id not in {p.id for p in self.state.players}:
            await err(f"Invalid target player: {chosen_player_id}")
            return
        if needs_card_choice:
            valid_card_ids = chosen_card_candidates(
                self.state, plan, player_id, card_id, chosen_player_id=chosen_player_id
            )
            if not valid_card_ids:
                pending.claimed_by = None  # unclaim; they may pass instead
                await err(f"There is no eligible target card for {self._card_title(card)}")
                return
            if chosen_card_id is None:
                await self.connections.send(
                    player_id,
                    self._prompt_choice_msg(
                        card_id,
                        f"Choose a target card for {self._card_title(card)}",
                        self._card_choice_payload(valid_card_ids),
                        cards=self._card_choice_snapshots(valid_card_ids),
                        chosen_player_id=chosen_player_id,
                        as_reaction=True,
                    ),
                )
                return
            if chosen_card_id not in valid_card_ids:
                await err(f"Invalid target card: {chosen_card_id}")
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
        with collect_hand_reveals() as reveals:
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
        await self._push_hand_reveals(reveals)
        await self._push_dice_rolls()
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
        """Register one stable setup slot and draft its mechanics off-lock."""
        if self._setup_slot_count(player_id) >= CARDS_TO_AUTHOR:
            await self.connections.send(
                player_id,
                {
                    "type": "error",
                    "message": (
                        f"You already have {CARDS_TO_AUTHOR} setup card slots. Revise or retry any failed card."
                    ),
                },
            )
            return

        card_id = str(uuid.uuid4())
        correlation_id = str(uuid.uuid4())
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
                "correlation_id": correlation_id,
                "draft_status": "drafting",
                "draft_reason": None,
                "draft_revision": 1,
                "draft_correlation_id": correlation_id,
            },
        }
        self.state = self.state.model_copy(update={"cards": new_cards})
        self._schedule_card_draft(card_id)
        await self._broadcast_state()

    async def _handle_redraft_card(self, player_id: str, msg) -> None:
        card = self.state.cards.get(msg.card_id)
        if not isinstance(card, dict) or card.get("creator_id") != player_id:
            await self.connections.send(player_id, {"type": "error", "message": "That setup card is not yours"})
            return
        if card.get("draft_status") != "failed":
            await self.connections.send(
                player_id,
                {"type": "error", "message": "Only a failed setup card can be revised or retried"},
            )
            return

        art = msg.art
        has_art = bool(card.get("has_art"))
        if art is not None:
            if self._replace_card_art(msg.card_id, art):
                has_art = True
            else:
                await self.connections.send(
                    player_id,
                    {"type": "error", "message": "This room's art storage is full — keeping the previous art"},
                )

        revision = int(card.get("draft_revision", 1)) + 1
        correlation_id = str(uuid.uuid4())
        updated = {
            **card,
            "title": msg.title,
            "description": msg.description,
            "has_art": has_art,
            "verdict": None,
            "draft_status": "drafting",
            "draft_reason": None,
            "draft_revision": revision,
            "draft_correlation_id": correlation_id,
            "correlation_id": correlation_id,
        }
        for key in ("canonical", "ops", "sandbox", "attributes", "agent_comment"):
            updated.pop(key, None)
        self.state = self.state.model_copy(update={"cards": {**self.state.cards, msg.card_id: updated}})
        self._schedule_card_draft(msg.card_id)
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
                reason = "The arbiter couldn't build this one."
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

    # ── voted host corrections ──

    async def _handle_admin_view(self, player_id: str, msg) -> None:
        if not msg.open:
            self.clear_admin_view(player_id)
            return
        if self.state.phase != "playing":
            await self.connections.send(
                player_id,
                {"type": "error", "message": "God mode is only available during play"},
            )
            return
        if not self._is_god_host(player_id):
            await self.connections.send(
                player_id,
                {"type": "error", "message": "God mode requires a spectator host"},
            )
            return
        if self._pending_admin is not None:
            await self.connections.send(
                player_id,
                {"type": "error", "message": "God mode is unavailable during a table vote"},
            )
            return
        self._admin_viewers.add(player_id)
        await self.connections.send(
            player_id,
            {
                "type": "admin_state",
                "state": redact_snapshot(self.snapshot(), player_id, reveal_all_cards=True),
            },
        )

    async def _handle_admin_propose(self, player_id: str, msg) -> None:
        if not self._is_host(player_id):
            await self.connections.send(player_id, {"type": "error", "message": "Only the host can propose changes"})
            return
        if self.state.phase not in {"playing", "results"}:
            await self.connections.send(
                player_id,
                {"type": "error", "message": "Host corrections are only available during play or results"},
            )
            return
        if (
            self._pending_admin is not None
            or self._pending is not None
            or self._pending_resolution is not None
            or self._pending_auto_play is not None
            or self._resolving_play is not None
        ):
            await self.connections.send(
                player_id,
                {"type": "error", "message": "Wait for the current table action to finish"},
            )
            return

        rng_seed = random.SystemRandom().randrange(2**63)
        try:
            application = apply_admin_actions(
                self.state,
                list(msg.actions),
                player_id,
                rng_seed=rng_seed,
                allow_hidden_sources=self._is_god_host(player_id),
            )
        except (KeyError, ValueError) as exc:
            await self.connections.send(player_id, {"type": "error", "message": str(exc)})
            return

        required = [player.id for player in self.state.players if player.id != player_id]
        proposal = PendingAdminProposal(
            proposal_id=uuid.uuid4().hex,
            proposer_id=player_id,
            phase=self.state.phase,
            actions=list(msg.actions),
            required_voter_ids=required,
            deadline_at=datetime.now(UTC) + timedelta(seconds=ADMIN_PROPOSAL_TIMEOUT_SECONDS),
            rng_seed=rng_seed,
            preview=application.preview,
            warnings=application.warnings,
        )
        self.clear_admin_view(player_id)
        self._pending_admin = proposal
        await self._pause_turn_timer()
        if not required:
            await self._apply_admin_proposal()
            return
        self._schedule_admin_timeout()
        await self._broadcast_state()

    async def _handle_admin_vote(self, player_id: str, msg) -> None:
        proposal = self._pending_admin
        if proposal is None or msg.proposal_id != proposal.proposal_id:
            await self.connections.send(player_id, {"type": "error", "message": "Proposal is no longer active"})
            return
        if player_id not in proposal.required_voter_ids:
            await self.connections.send(player_id, {"type": "error", "message": "You are not a voter on this proposal"})
            return
        if player_id in proposal.approvals:
            await self.connections.send(player_id, {"type": "error", "message": "Your vote is already locked in"})
            return
        if datetime.now(UTC) >= proposal.deadline_at:
            await self._finish_admin_proposal("expired", "The host proposal expired without unanimous approval.")
            return
        if not msg.accept:
            await self._finish_admin_proposal("rejected", "The table rejected the host proposal.")
            return
        approvals = [*proposal.approvals, player_id]
        self._pending_admin = proposal.model_copy(update={"approvals": approvals})
        if len(approvals) == len(proposal.required_voter_ids):
            await self._apply_admin_proposal()
        else:
            await self._broadcast_state()

    async def _handle_admin_cancel(self, player_id: str, msg) -> None:
        proposal = self._pending_admin
        if proposal is None or msg.proposal_id != proposal.proposal_id:
            await self.connections.send(player_id, {"type": "error", "message": "Proposal is no longer active"})
            return
        if not self._is_host(player_id) or proposal.proposer_id != player_id:
            await self.connections.send(
                player_id, {"type": "error", "message": "Only the host can cancel this proposal"}
            )
            return
        await self._finish_admin_proposal("cancelled", "The host cancelled the proposal.")

    def _cancel_admin_timer(self) -> None:
        if (
            self._admin_timer is not None
            and self._admin_timer is not asyncio.current_task()
            and not self._admin_timer.done()
        ):
            self._admin_timer.cancel()
        self._admin_timer = None

    def _schedule_admin_timeout(self) -> None:
        proposal = self._pending_admin
        if proposal is None:
            return
        self._cancel_admin_timer()
        delay = max(0.0, (proposal.deadline_at - datetime.now(UTC)).total_seconds())
        self._admin_timer = asyncio.create_task(self._admin_timeout(proposal.proposal_id, delay))

    async def _admin_timeout(self, proposal_id: str, delay: float) -> None:
        await asyncio.sleep(delay)
        async with self._lock:
            proposal = self._pending_admin
            if proposal is None or proposal.proposal_id != proposal_id:
                return
            await self._finish_admin_proposal("expired", "The host proposal expired without unanimous approval.")
            self._notify_change()

    def _record_admin_audit(self, proposal: PendingAdminProposal, outcome: str) -> None:
        self.state = append_history_event(
            self.state,
            "admin_change",
            actor_id=proposal.proposer_id,
            target_player_ids=list(proposal.required_voter_ids),
            source=outcome,
            data={
                "proposal_id": proposal.proposal_id,
                "outcome": outcome,
                "actions": [
                    {"kind": item.kind, "title": item.title, "detail": item.detail} for item in proposal.preview
                ],
            },
        )

    async def _broadcast_admin_result(
        self,
        proposal: PendingAdminProposal,
        outcome: str,
        message: str,
    ) -> None:
        await self.connections.broadcast(
            {
                "type": "admin_proposal_result",
                "proposal_id": proposal.proposal_id,
                "outcome": outcome,
                "message": message,
            }
        )

    async def _finish_admin_proposal(self, outcome: str, message: str) -> None:
        proposal = self._pending_admin
        if proposal is None:
            return
        self._cancel_admin_timer()
        self._pending_admin = None
        self._record_admin_audit(proposal, outcome)
        await self._log_and_broadcast(message)
        await self._broadcast_admin_result(proposal, outcome, message)
        await self._broadcast_state()
        await self._maybe_resume_turn_timer()

    async def _apply_admin_proposal(self) -> None:
        proposal = self._pending_admin
        if proposal is None:
            return
        self._cancel_admin_timer()
        try:
            application = apply_admin_actions(
                self.state,
                proposal.actions,
                proposal.proposer_id,
                rng_seed=proposal.rng_seed,
                allow_hidden_sources=self._is_god_host(proposal.proposer_id),
            )
        except KeyError, ValueError:
            await self._finish_admin_proposal(
                "cancelled",
                "The host proposal was no longer valid and was cancelled.",
            )
            return

        self.state = application.state
        self._pending_admin = None
        self._deck_exhausted = self.state.phase == "playing" and not self.state.deck
        self._record_admin_audit(proposal, "applied")
        message = "The table unanimously approved the host proposal."

        if application.ends_game:
            await self._end_game(emit_hooks=False)
        elif application.active_player_eliminated and self.state.phase == "playing":
            if self._deck_exhausted or self._end_now() or win_condition_met(self.state):
                await self._end_game()
            else:
                self.state = advance_turn(self.state)
                await self._start_turn(self.state.active_player().id)
        else:
            await self._broadcast_state()
            await self._maybe_resume_turn_timer()

        await self._log_and_broadcast(message)
        await self._broadcast_admin_result(proposal, "applied", message)

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
        if not self._epilogue.record_vote(player_id, msg.card_id, msg.keep):
            await self.connections.send(
                player_id, {"type": "error", "message": "Vote rejected: not an eligible voter or unknown card"}
            )

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

        Surfaces the outcome as ``state.epilogue_result`` (id+title per card,
        plus the table-favorite ids) so the final results screen — and a client
        reconnecting after the vote — can render kept/destroyed lists and the
        favorite highlight straight from the snapshot.
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
            favorite_card_ids=list(result.favorites),
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
        snap["setup_draft_progress"] = self._setup_draft_progress()
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
        # Deck-top interactions (card_order / from_deck_top card_pick) entitle
        # their audience — and no one else — to the offered cards' content
        # while the pause lasts. redact_snapshot consumes (and always strips)
        # this: it keeps the listed registry entries for listed viewers only,
        # so a reconnecting scryer can still render the faces.
        deck_top_count = self._deck_top_count(pending.request) if pending is not None else None
        snap["interaction_card_visibility"] = (
            {
                "viewer_ids": list(pending.resolved_audience),
                "card_ids": self._deck_top_options(deck_top_count),
            }
            if deck_top_count is not None
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
        # Live turn clock (reconnect-safe source of truth; the turn_timer push
        # is the immediacy signal). Null when no clock is armed.
        snap["turn_timer"] = self._turn_timer_snapshot()
        pending_admin = self._pending_admin
        snap["pending_admin_proposal"] = (
            {
                "proposal_id": pending_admin.proposal_id,
                "proposer_id": pending_admin.proposer_id,
                "phase": pending_admin.phase,
                "deadline_at": pending_admin.deadline_at.isoformat(),
                "preview": [item.model_dump() for item in pending_admin.preview],
                "warnings": list(pending_admin.warnings),
                "voters": [
                    {
                        "player_id": voter_id,
                        "status": "approved" if voter_id in pending_admin.approvals else "waiting",
                    }
                    for voter_id in pending_admin.required_voter_ids
                ],
            }
            if pending_admin is not None
            else None
        )
        return snap

    def snapshot_for(self, viewer_id: str | None) -> dict:
        """:meth:`snapshot` redacted for one viewer (see board.rooms.redaction).

        The viewer keeps their own hand; other hands become counts, and the
        draw pile becomes a count once the game has started. ``None`` — or any
        id that is not a seated player, i.e. a spectator — gets the
        fully-hidden view. Every snapshot that leaves the server for a client
        must go through here.
        """
        return redact_snapshot(self.snapshot(), viewer_id)

    def admin_snapshot_for(self, viewer_id: str) -> dict:
        """Full card-state projection for an authorized open God-mode panel."""
        if self.state.phase != "playing" or not self._is_god_host(viewer_id):
            raise ValueError("God mode requires a spectator host during play")
        return redact_snapshot(self.snapshot(), viewer_id, reveal_all_cards=True)

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
        snap = self.snapshot()
        await self.connections.broadcast_state(lambda viewer_id: redact_snapshot(snap, viewer_id))
        if self.state.phase != "playing":
            self._admin_viewers.clear()
            return
        for viewer_id in list(self._admin_viewers):
            if not self._is_god_host(viewer_id):
                self._admin_viewers.discard(viewer_id)
                continue
            await self.connections.send(
                viewer_id,
                {
                    "type": "admin_state",
                    "state": redact_snapshot(snap, viewer_id, reveal_all_cards=True),
                },
            )
