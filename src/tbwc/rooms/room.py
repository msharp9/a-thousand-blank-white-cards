"""tbwc.rooms.room — one game session: GameState + ConnectionManager + turn enforcement.

Room owns an immutable GameState (replaced on each mutation) and serialises all
handle_action calls with an asyncio.Lock so concurrent WebSocket messages cannot
corrupt turn order. Play/pass require the active player's turn; card creation and
preview are allowed off-turn. Play and create_card run the agent interpretation
graph via asyncio.to_thread (with a "brewing" broadcast), applying resulting
effects through the engine.

Turn model (draw → play → end turn): drawing is EXPLICIT. A turn has three
steps: (1) the active player sends ``draw`` to take ``draw_count`` card(s) — once
per turn, and required before playing/ending while the deck is non-empty; (2)
they ``play`` a card OR (3) ``pass`` / ``end_turn`` to end without playing. Either
ending advances the turn; the next player's draw flag resets and they must draw
themselves. There is no auto-draw.

End game: when a player draws the LAST card of the deck, ``_deck_exhausted``
latches. That player finishes their turn normally; when their turn ends the game
transitions to ``phase="ended"`` with computed ``winner_ids`` broadcast to
everyone. (Per the rules: the player who draws the last card completes their
turn, then the game ends.)
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from tbwc.engine.apply import apply_effect
from tbwc.engine.compile import compile_card
from tbwc.engine.events import GameEvent, HookContext
from tbwc.engine.loop import advance_turn
from tbwc.engine.scoring import evaluate_win_condition
from tbwc.models.effects import CustomNoteOp, EffectProgram
from tbwc.models.game_state import GameState, Player
from tbwc.rooms.connections import ConnectionManager
from tbwc.rooms.deck import (
    BLANKS_PER_PLAYER,
    PREMADE_POOL_SIZE,
    build_premade_pool,
    finalize_deck,
)
from tbwc.rooms.epilogue import EpilogueManager

logger = logging.getLogger(__name__)

# Cards dealt to each player's hand when the game starts.
STARTING_HAND_SIZE = 5

# Cards each player must author during the setup phase before the game can start.
CARDS_TO_AUTHOR = BLANKS_PER_PLAYER


class Room:
    """One game session. Thread-safe via asyncio.Lock."""

    def __init__(self, code: str, mode: str = "both", *, simple: bool = True) -> None:
        self.code = code
        self.state: GameState = GameState(room_code=code, mode=mode)
        self.connections: ConnectionManager = ConnectionManager()
        self._lock: asyncio.Lock = asyncio.Lock()
        self._epilogue: EpilogueManager | None = None
        # Whether to seed the pre-made pool from the deterministic point-only
        # simple deck (the basic no-AI game). Kept as an attribute so tests and
        # future modes can flip it; defaults True for the basic game.
        self._simple = simple
        # Per-turn bookkeeping for the draw→play→end model. Reset at the start of
        # every turn (see _start_turn). ``_has_drawn`` gates play/pass so a turn
        # follows draw-first; ``_deck_exhausted`` latches once the last card is
        # drawn so the game ends after the drawer finishes their turn.
        self._has_drawn: bool = False
        self._deck_exhausted: bool = False

    # ── player management ──
    def add_player(self, player_id: str, name: str, spectator: bool = False) -> None:
        """Append a player to the immutable GameState (reassigns self.state).

        ``spectator=True`` flags a late joiner (game already left the lobby):
        they still live in ``players`` so they receive state and appear on the
        table, but they are excluded from the turn rotation, dealing, card
        authoring and win scoring. The join *policy* (who becomes a spectator)
        lives in :meth:`RoomManager.join`, which decides the flag from the
        room's phase; this method just records it.
        """
        new_players = [*self.state.players, Player(id=player_id, name=name, spectator=spectator)]
        self.state = self.state.model_copy(update={"players": new_players})

    def get_player_ids(self) -> list[str]:
        return [p.id for p in self.state.players]

    def _is_spectator(self, player_id: str) -> bool:
        return any(p.id == player_id and p.spectator for p in self.state.players)

    # ── turn helpers ──
    def _is_active_player(self, player_id: str) -> bool:
        if not self.state.players:
            return False
        idx = self.state.turn_index % len(self.state.players)
        return self.state.players[idx].id == player_id

    # ── main dispatch ──
    async def handle_action(self, player_id: str, msg) -> None:
        """Serialised entry point for all client messages."""
        async with self._lock:
            await self._dispatch(player_id, msg)

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
        elif mtype == "epilogue_vote":
            await self._handle_epilogue_vote(player_id, msg)
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
        # authored + blank cards at finalisation.
        merged_cards = {**cards, **self.state.cards}
        self.state = self.state.model_copy(update={"phase": "setup", "cards": merged_cards, "deck": list(pool)})
        await self._broadcast_state()

    async def _start_playing(self, player_id: str) -> None:
        """setup → playing: gate on authoring, finalise deck, deal, begin play."""
        players = list(self.state.players)
        dealt_to = [p for p in players if not p.spectator]

        # Gate: every real player must have authored the required number of cards.
        behind = [p for p in dealt_to if self._authored_count(p.id) < CARDS_TO_AUTHOR]
        if behind:
            names = ", ".join(self._name(p.id) for p in behind)
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
            }
        )
        # Begin the first player's turn (draw → play → end model): no auto-draw.
        if self.state.players:
            await self._start_turn(self.state.active_player().id)
        await self._broadcast_state()

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
        await self._broadcast_state()

    async def _handle_draw(self, player_id: str) -> None:
        """Active player draws their ``draw_count`` card(s) — the first turn step.

        Enforces one draw per turn. Drawing the last card of the deck latches
        ``_deck_exhausted`` so the game ends after this player finishes their
        turn (per the rules: the player who draws the last card completes their
        turn, then the game ends).
        """
        if self._has_drawn:
            await self.connections.send(player_id, {"type": "error", "message": "You have already drawn this turn"})
            return
        if not self.state.deck:
            # Nothing left to draw; mark drawn so the player can still play/pass.
            self._has_drawn = True
            await self.connections.send(player_id, {"type": "error", "message": "The deck is empty — nothing to draw"})
            return

        self._draw_cards(player_id, self.state.draw_count)
        self._has_drawn = True
        if not self.state.deck:
            # The last card(s) were just drawn — the game ends when this turn ends.
            self._deck_exhausted = True
        await self._log_and_broadcast(f"{self._name(player_id)} drew a card")

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
        """End the current turn: end the game if the deck is exhausted, else
        advance to the next player and start their turn.

        Reuses ``engine.loop.advance_turn`` so direction, skip-next, extra-turn
        and any registered skip predicate are all honoured — those flags are set
        by the reducers during a play's apply_effect. Runs under the caller's
        lock, so advance is a single serialized operation with no interleaving.

        End-of-game timing: once the deck has been exhausted (the last card was
        drawn this game), the player who drew it finishes their turn and THEN the
        game ends here — matching the rule "the last card is drawn, that player
        finishes their turn, then the game ends".
        """
        if not self.state.players:
            return
        if self._deck_exhausted:
            await self._end_game()
            return
        self.state = advance_turn(self.state)
        await self._start_turn(self.state.active_player().id)

    def _name(self, player_id: str) -> str:
        """Human-readable display name for a player id (falls back to the id)."""
        for p in self.state.players:
            if p.id == player_id:
                return p.name
        return player_id

    async def _handle_pass(self, player_id: str) -> None:
        """Active player ends their turn without playing a card."""
        await self._log_and_broadcast(f"{self._name(player_id)} passed")
        await self._advance_turn()

    async def _end_game(self) -> None:
        """Transition to phase='ended', compute winners, and broadcast to everyone.

        Chosen approach: go straight to ``ended`` with computed winners rather
        than the epilogue voting flow (start_epilogue requires the separate
        EpilogueManager concern). ``evaluate_win_condition`` honours the state's
        win_condition (default highest_points). The winners are stored on the
        state (winner_ids) AND logged so all connected players — not just the
        active one — see the result on the broadcast.
        """
        winners = evaluate_win_condition(self.state)
        if winners:
            names = [self.state.get_player(w).name for w in winners]
            log_line = f"Game over! Winner(s): {', '.join(names)}"
        else:
            log_line = "Game over! No winner."
        self.state = self.state.model_copy(update={"phase": "ended", "winner_ids": winners}).with_log(log_line)
        await self._broadcast_state()
        await self.connections.broadcast({"type": "effect_applied", "log_entry": log_line})

    def _is_blank(self, card) -> bool:
        """True if ``card`` is an un-authored blank (blank flag still set)."""
        if isinstance(card, dict):
            return bool(card.get("blank"))
        return bool(getattr(card, "blank", False))

    def _card_is_playable(self, card) -> bool:
        """True if a card in hand can meaningfully be played.

        A card is playable if it is a blank (blanks are ALWAYS playable — they're
        authored on play), OR it compiles to a non-empty program, OR it carries
        free text the LLM could interpret. In practice nearly every card is
        playable; the only truly inert card is an empty, canonical-less,
        description-less entry. This deliberately errs toward "playable" so we
        never force a pass when the player actually has options.
        """
        if self._is_blank(card):
            return True
        card_dict = card if isinstance(card, dict) else card.model_dump()
        program = compile_card(card_dict)
        if program is not None and program.ops:
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

    async def _resolve_program(self, card_id: str, card) -> EffectProgram:
        """Return the EffectProgram to apply for a played card — NEVER None.

        Resolution order (the deterministic basic game must not depend on the LLM):

        1. **Compiled ops** — if the card carries structured ops (a gold/simple
           card, or a blank authored with a canonical), ``compile_card`` turns
           them into a runtime program. This path is fully deterministic and
           never calls the agent; the simple seed deck lives entirely here.
        2. **LLM interpretation (best-effort)** — for free-text cards with no
           compilable ops (most authored blanks), we ask the agent to interpret
           the title/description. This runs in a thread and is broadcast as
           ``brewing``/``card_interpreted`` for UI feedback. If it yields a valid
           program we use it.
        3. **Deterministic fallback** — if neither produced ops (no canonical AND
           the LLM was unavailable / returned nothing / an invalid verdict), we
           return a single ``CustomNoteOp`` so the play still resolves with a log
           line instead of silently doing nothing. A play NEVER silently no-ops.
        """
        title = card["title"] if isinstance(card, dict) else getattr(card, "title", "")
        description = card["description"] if isinstance(card, dict) else getattr(card, "description", "")

        # 1. Deterministic compiled path — no LLM.
        compiled = compile_card(card if isinstance(card, dict) else card.model_dump())
        if compiled is not None and compiled.ops:
            return compiled

        # 2. Best-effort LLM interpretation for free-text cards.
        from tbwc.agent.graph import interpret_card

        await self.connections.broadcast({"type": "brewing", "card_id": card_id})
        try:
            result = await asyncio.to_thread(interpret_card, title, description)
        except Exception:
            logger.exception("interpret_card failed for %s; using deterministic fallback", card_id)
            result = {"verdict": "error", "program": None, "snippet": None}

        await self.connections.broadcast(
            {
                "type": "card_interpreted",
                "card_id": card_id,
                "program": str(result.get("program")) if result.get("program") is not None else None,
                "snippet": getattr(result.get("snippet"), "code", None),
                "verdict": result.get("verdict", "error"),
            }
        )

        program = result.get("program")
        if result.get("verdict") == "ok" and program is not None and getattr(program, "ops", None):
            return program

        # 3. Deterministic fallback — never a silent no-op.
        note = title or "Card"
        return EffectProgram(ops=[CustomNoteOp(note=f"Played {note} (no mechanical effect)")])

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

        Resolution (see :meth:`_resolve_program`) prefers the deterministic
        compiled-ops path and falls back to the LLM then to a CustomNoteOp, so a
        play always resolves to a program and never silently no-ops.
        """
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
            authored = {**card, "title": title, "description": description, "creator_id": player_id}
            authored.pop("blank", None)
            merged = {**self.state.cards, card_id: authored}
            self.state = self.state.model_copy(update={"cards": merged})
            card = authored

        title = card["title"] if isinstance(card, dict) else getattr(card, "title", "")
        program = await self._resolve_program(card_id, card)

        if program is not None and program.ops:
            # Cards whose program targets "chooser"/"target_player"
            # need a chosen_player_id; those with a "chosen_card" CardTarget need
            # a chosen_card_id. Validate BEFORE applying so a missing/bogus choice
            # yields a clean error rather than a 500 out of the reducers'
            # _resolve_targets / _resolve_card_targets — and does NOT advance the
            # turn. We inspect the ops to tell WHICH kind of choice is needed
            # (requires_choice alone conflates the two axes).
            chosen_player_id = getattr(msg, "chosen_player_id", None)
            chosen_card_id = getattr(msg, "chosen_card_id", None)
            valid_player_ids = {p.id for p in self.state.players}
            # A card the actor may pick: anything currently in play or in their hand.
            valid_card_ids = set(self.state.cards_in_play()) | set(self.state.get_player(player_id).hand)

            ops = getattr(program, "ops", [])
            needs_player_choice = any(
                getattr(op, field, None) in ("chooser", "target_player")
                for op in ops
                for field in ("target", "from_target", "to_target")
            )
            needs_card_choice = any(getattr(op, "card_target", None) == "chosen_card" for op in ops)

            if needs_player_choice and chosen_player_id is None:
                # Instead of erroring, ask the active player who to target. The
                # play is held PENDING: the turn does NOT advance and the card is
                # not consumed. The client answers with a second `play` message
                # carrying chosen_player_id, which re-runs this handler (we do NOT
                # keep server-side pending state — see module note / bead jcc).
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
                # Card-target axis: also prompt rather than error. Choices are the
                # cards the actor may legally pick (in-play zone + their hand).
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
            self.state = apply_effect(self.state, program, ctx)
            await self._log_and_broadcast(f"Played {card_id}")

        # Terminal apply path: the play is committed (all rejection / pending
        # early-returns above have already returned, so the card is NOT removed
        # when the play is rejected or held for a prompt_choice). Move the played
        # card out of the hand into its destination zone. to_player_id only
        # matters for the in_play destination; it is harmless for center/discard.
        dest = self._play_destination(card)
        self.state = self.state.move_card(card_id, "hand", dest, from_player_id=player_id, to_player_id=player_id)

        # Playing a card ends the turn: advance (honouring skip/extra/direction
        # flags the play may have set) and start the next player's turn, which
        # auto-draws or ends the game on an empty deck.
        await self._broadcast_state()
        await self._advance_turn()

    async def _handle_create_card(self, player_id: str, msg) -> None:
        """Author a new card (allowed off-turn / during setup).

        During ``setup`` we DO NOT call the LLM: authored cards are interpreted
        deterministically (via ``compile_card``) or best-effort at play time, so
        setup authoring stays fast and never depends on a live service. The card
        is simply registered with its ``creator_id`` (which drives
        ``setup_progress`` and the start gate) and broadcast.
        """
        card_id = str(uuid.uuid4())
        new_cards = {
            **self.state.cards,
            card_id: {
                "id": card_id,
                "title": msg.title,
                "description": msg.description,
                "creator_id": player_id,
            },
        }
        self.state = self.state.model_copy(update={"cards": new_cards})

        if self.state.phase == "setup":
            await self._broadcast_state()
            return

        from tbwc.agent.graph import interpret_card

        await self.connections.broadcast({"type": "brewing", "card_id": card_id})
        result = await asyncio.to_thread(interpret_card, msg.title, msg.description)

        # store interpretation summary on the card
        card = {
            **self.state.cards[card_id],
            "program": str(result.get("program")) if result.get("program") is not None else None,
            "snippet": getattr(result.get("snippet"), "code", None),
            "verdict": result["verdict"],
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
            }
        )
        await self._broadcast_state()

    async def _handle_preview_card(self, player_id: str, msg) -> None:
        await self.connections.send(
            player_id,
            {"type": "preview_result", "program": None, "snippet": msg.description, "verdict": "ok"},
        )

    async def start_epilogue(self) -> None:
        """Begin the epilogue phase: gather created cards and open voting."""
        cards = list(self.state.cards.values())
        # normalise to dicts with an 'id' key
        card_dicts = [c if isinstance(c, dict) else c.model_dump() for c in cards]
        # Only real players vote in the epilogue; spectators authored no cards
        # and must not be counted as expected voters (which would stall the tally).
        self._epilogue = EpilogueManager(player_ids=[p.id for p in self.state.turn_players()])
        self.state = self.state.model_copy(update={"phase": "epilogue"})
        await self._epilogue.start(card_dicts, self.connections)
        await self._broadcast_state()

    async def _handle_epilogue_vote(self, player_id: str, msg) -> None:
        if self._epilogue is None:
            await self.connections.send(player_id, {"type": "error", "message": "No epilogue in progress"})
            return
        all_in = self._epilogue.record_vote(player_id, msg.card_id, msg.keep)
        if all_in:
            result = await self._epilogue.tally_and_persist()
            self.state = self.state.model_copy(update={"phase": "ended"})
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

    async def _broadcast_state(self) -> None:
        await self.connections.broadcast_state(self.snapshot())
