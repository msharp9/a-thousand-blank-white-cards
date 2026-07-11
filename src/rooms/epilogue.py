"""rooms.epilogue — epilogue vote lifecycle for one Room.

Collects keep/destroy votes on cards created during the game, tallies them via the
phase-1 tally logic, and upserts kept cards into the RAG corpus so future games can
draw them. No UI concerns here (broadcast envelopes only).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from rooms.connections import ConnectionManager

logger = logging.getLogger(__name__)


class EpilogueManager:
    """Manages the epilogue vote lifecycle for one Room."""

    def __init__(self, player_ids: list[str]) -> None:
        self._player_ids = list(player_ids)
        self._votes: dict[str, dict[str, str]] = {}  # card_id -> {player_id: "keep"|"destroy"}
        self._cards: list[dict[str, Any]] = []
        self._connections: ConnectionManager | None = None

    async def start(self, cards: list[dict[str, Any]], connections: ConnectionManager) -> None:
        """Begin the epilogue: broadcast the 'epilogue' envelope with all created cards."""
        self._cards = cards
        self._connections = connections
        for card in cards:
            self._votes[card["id"]] = {}
        await connections.broadcast({"type": "epilogue", "cards": cards})
        logger.info("epilogue started: %d cards to vote on", len(cards))

    def record_vote(self, player_id: str, card_id: str, keep: bool) -> bool:
        """Record a keep/destroy vote. Returns True once all expected votes are in."""
        if card_id not in self._votes:
            return False
        self._votes[card_id][player_id] = "keep" if keep else "destroy"
        return self._all_votes_in()

    def _all_votes_in(self) -> bool:
        if not self._votes:
            return False
        return all(len(v) >= len(self._player_ids) for v in self._votes.values())

    async def tally_and_persist(self) -> Any:
        """Tally votes, upsert kept cards into RAG, return the EpilogueResult."""
        from engine.epilogue import tally_votes
        from rag.store import upsert_card

        card_ids = [c["id"] for c in self._cards]
        # tally_votes expects {player_id: {card_id: vote}}, but votes are stored
        # here as {card_id: {player_id: vote}} — transpose before tallying.
        per_player: dict[str, dict[str, str]] = {}
        for card_id, player_votes in self._votes.items():
            for player_id, vote in player_votes.items():
                per_player.setdefault(player_id, {})[card_id] = vote
        result = tally_votes(per_player, card_ids)

        kept_cards = [c for c in self._cards if c["id"] in result.kept]
        for card in kept_cards:
            try:
                await asyncio.to_thread(
                    upsert_card,
                    card_id=card["id"],
                    title=card.get("title", ""),
                    description=card.get("description", ""),
                    canonical=str(card.get("program") or ""),
                    source="player",
                )
                logger.info("upserted card %s into RAG corpus", card["id"])
            except Exception as exc:
                logger.warning("failed to upsert kept card %s (non-fatal): %s", card["id"], exc)

        logger.info("epilogue tally: %d kept, %d destroyed", len(result.kept), len(result.destroyed))
        return result
