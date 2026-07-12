"""board.rooms.epilogue — epilogue vote lifecycle for one Room.

Collects keep/destroy votes on cards created during the game, tallies them via the
phase-1 tally logic, and upserts kept cards into the RAG corpus so future games can
draw them. No UI concerns here (broadcast envelopes only).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from board.rooms.connections import ConnectionManager

logger = logging.getLogger(__name__)


class EpilogueManager:
    """Manages the epilogue vote lifecycle for one Room."""

    def __init__(self, player_ids: list[str]) -> None:
        self._player_ids = list(player_ids)
        self._votes: dict[str, dict[str, str]] = {}  # card_id -> {player_id: "keep"|"destroy"}
        self._done: set[str] = set()  # player_ids who have signalled epilogue_done
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

    def record_vote(self, player_id: str, card_id: str, keep: bool) -> None:
        """Record a keep/destroy vote for one card. Completion is driven
        separately by :meth:`mark_done` — a vote no longer implicitly finalizes
        anything, so a player can vote on some cards and skip the rest."""
        if card_id not in self._votes:
            return
        self._votes[card_id][player_id] = "keep" if keep else "destroy"

    def mark_done(self, player_id: str) -> bool:
        """Mark ``player_id`` as finished voting; any card they didn't vote on
        abstains (``tally_votes`` already treats a missing vote as abstain).

        Returns True once every expected (non-spectator) player is done, which
        is the room's cue to finalize — this replaces the old full-coverage
        gate so a player who walks away can't stall the room forever.
        """
        if player_id in self._player_ids:
            self._done.add(player_id)
        return self.all_done()

    def all_done(self) -> bool:
        """True once every expected player has signalled done."""
        if not self._player_ids:
            return False
        return set(self._player_ids) <= self._done

    def to_dict(self) -> dict[str, Any]:
        """Serialize in-progress vote state for persistence (see board.rooms.store).

        Excludes ``_connections`` — it's a live WebSocket registry, reattached by
        the room on restore rather than serialized.
        """
        return {
            "player_ids": list(self._player_ids),
            "votes": {card_id: dict(player_votes) for card_id, player_votes in self._votes.items()},
            "done": sorted(self._done),
            "cards": list(self._cards),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], connections: ConnectionManager) -> EpilogueManager:
        """Rehydrate from :meth:`to_dict` output, reattaching ``connections``.

        This restores an already-in-progress vote (a reload), so it must NOT
        re-broadcast the 'epilogue' envelope the way :meth:`start` does.
        """
        mgr = cls(player_ids=data.get("player_ids", []))
        mgr._votes = {card_id: dict(player_votes) for card_id, player_votes in data.get("votes", {}).items()}
        mgr._done = set(data.get("done", []))
        mgr._cards = list(data.get("cards", []))
        mgr._connections = connections
        return mgr

    async def tally_and_persist(self) -> Any:
        """Tally votes on top of prior cross-game totals, persist the decision.

        Keep/destroy is decided on CUMULATIVE totals, not this game's votes
        alone: a card's prior keep/destroy counts (if it's already in the RAG
        corpus from an earlier game) are loaded, this game's votes are added on
        top, and the verdict follows the combined total (ties keep). Kept cards
        are upserted with their updated running totals; destroyed cards are
        removed from the corpus so they stop re-entering future decks.
        """
        from engine.epilogue import tally_votes
        from agent.rag.store import delete_card, get_card_totals, upsert_card

        card_ids = [c["id"] for c in self._cards]
        # tally_votes expects {player_id: {card_id: vote}}, but votes are stored
        # here as {card_id: {player_id: vote}} — transpose before tallying.
        per_player: dict[str, dict[str, str]] = {}
        for card_id, player_votes in self._votes.items():
            for player_id, vote in player_votes.items():
                per_player.setdefault(player_id, {})[card_id] = vote

        prior_totals: dict[str, tuple[int, int]] = {}
        for card_id in card_ids:
            try:
                totals = await asyncio.to_thread(get_card_totals, card_id)
            except Exception as exc:
                logger.warning("failed to fetch prior totals for %s (treated as new): %s", card_id, exc)
                totals = None
            if totals is not None:
                prior_totals[card_id] = totals

        result = tally_votes(per_player, card_ids, prior_totals=prior_totals)
        tallies_by_id = {t.card_id: t for t in result.tallies}

        kept_cards = [c for c in self._cards if c["id"] in result.kept]
        for card in kept_cards:
            tally = tallies_by_id[card["id"]]
            try:
                await asyncio.to_thread(
                    upsert_card,
                    card_id=card["id"],
                    title=card.get("title", ""),
                    description=card.get("description", ""),
                    canonical=str(card.get("program") or ""),
                    source="player",
                    keep_votes=tally.keep_votes,
                    destroy_votes=tally.destroy_votes,
                )
                logger.info(
                    "upserted card %s into RAG corpus (totals %d-%d)", card["id"], tally.keep_votes, tally.destroy_votes
                )
            except Exception as exc:
                logger.warning("failed to upsert kept card %s (non-fatal): %s", card["id"], exc)

        destroyed_cards = [c for c in self._cards if c["id"] in result.destroyed]
        for card in destroyed_cards:
            try:
                await asyncio.to_thread(delete_card, card["id"])
                logger.info("retired card %s from RAG corpus", card["id"])
            except Exception as exc:
                logger.warning("failed to retire destroyed card %s (non-fatal): %s", card["id"], exc)

        logger.info("epilogue tally: %d kept, %d destroyed", len(result.kept), len(result.destroyed))
        return result
