"""tbwc.rooms.room — one game session: GameState + ConnectionManager + turn enforcement.

Room owns an immutable GameState (replaced on each mutation) and serialises all
handle_action calls with an asyncio.Lock so concurrent WebSocket messages cannot
corrupt turn order. Draw/play require the active player's turn; card creation and
preview are allowed off-turn. Play and create_card run the agent interpretation
graph via asyncio.to_thread (with a "brewing" broadcast), applying resulting
effects through the engine.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from tbwc.engine.apply import apply_effect
from tbwc.engine.events import GameEvent, HookContext
from tbwc.models.game_state import GameState, Player
from tbwc.rooms.connections import ConnectionManager

logger = logging.getLogger(__name__)


class Room:
    """One game session. Thread-safe via asyncio.Lock."""

    def __init__(self, code: str) -> None:
        self.code = code
        self.state: GameState = GameState(room_code=code)
        self.connections: ConnectionManager = ConnectionManager()
        self._lock: asyncio.Lock = asyncio.Lock()

    # ── player management ──
    def add_player(self, player_id: str, name: str) -> None:
        """Append a player to the immutable GameState (reassigns self.state)."""
        new_players = [*self.state.players, Player(id=player_id, name=name)]
        self.state = self.state.model_copy(update={"players": new_players})

    def get_player_ids(self) -> list[str]:
        return [p.id for p in self.state.players]

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
        if mtype == "start":
            await self._handle_start(player_id)
        elif mtype == "draw":
            if not self._is_active_player(player_id):
                await self.connections.send(player_id, {"type": "error", "message": "Not your turn"})
                return
            await self._handle_draw(player_id)
        elif mtype == "play":
            if not self._is_active_player(player_id):
                await self.connections.send(player_id, {"type": "error", "message": "Not your turn"})
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

    # ── per-action handlers (engine-backed minimal versions; agent interpret in a later bead) ──
    async def _handle_start(self, player_id: str) -> None:
        self.state = self.state.model_copy(update={"phase": "playing"})
        await self._broadcast_state()

    async def _handle_draw(self, player_id: str) -> None:
        if not self.state.deck:
            await self.connections.send(player_id, {"type": "error", "message": "Deck is empty"})
            return
        drawn, *rest = self.state.deck
        new_players = [
            p.model_copy(update={"hand": [*p.hand, drawn]}) if p.id == player_id else p for p in self.state.players
        ]
        self.state = self.state.model_copy(update={"deck": rest, "players": new_players})
        await self._broadcast_state()

    async def _handle_play(self, player_id: str, msg) -> None:
        """Interpret the played card via the agent (in a thread), apply its effect, advance turn."""
        from tbwc.agent.graph import interpret_card

        card_id = msg.card_id
        card = self.state.cards.get(card_id)
        if card is None:
            await self.connections.send(player_id, {"type": "error", "message": f"Card {card_id} not found"})
            return

        await self.connections.broadcast({"type": "brewing", "card_id": card_id})

        title = card["title"] if isinstance(card, dict) else getattr(card, "title", "")
        description = card["description"] if isinstance(card, dict) else getattr(card, "description", "")
        result = await asyncio.to_thread(interpret_card, title, description)

        await self.connections.broadcast(
            {
                "type": "card_interpreted",
                "card_id": card_id,
                "program": str(result.get("program")) if result.get("program") is not None else None,
                "snippet": getattr(result.get("snippet"), "code", None),
                "verdict": result["verdict"],
            }
        )

        program = result.get("program")
        if result["verdict"] == "ok" and program is not None:
            ctx = HookContext(event=GameEvent.ON_PLAY, actor_id=player_id, card_id=card_id)
            self.state = apply_effect(self.state, program, ctx)
            await self.connections.broadcast({"type": "effect_applied", "log_entry": f"Played {card_id}"})

        # advance turn
        n = len(self.state.players)
        if n:
            self.state = self.state.model_copy(
                update={"turn_index": (self.state.turn_index + self.state.direction) % n}
            )
        await self._broadcast_state()

    async def _handle_create_card(self, player_id: str, msg) -> None:
        """Author a new card and interpret it immediately (allowed off-turn)."""
        from tbwc.agent.graph import interpret_card

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

    async def _handle_epilogue_vote(self, player_id: str, msg) -> None:
        pass  # handled in the epilogue-flow bead

    # ── helpers ──
    def snapshot(self) -> dict:
        """JSON-serialisable snapshot of the current GameState."""
        return self.state.model_dump()

    async def _broadcast_state(self) -> None:
        await self.connections.broadcast_state(self.snapshot())
