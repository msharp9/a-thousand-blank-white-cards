"""engine.epilogue — pure vote-tallying logic for the end-of-game epilogue.

Players vote keep/destroy on each card created during the session. Majority
keep wins; ties default to keep. No UI/WebSocket concerns here.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class CardVotes(BaseModel):
    """Votes for a single card."""

    card_id: str
    keep_votes: int = 0
    destroy_votes: int = 0

    def verdict(self) -> str:
        """'keep' wins on tie."""
        return "keep" if self.keep_votes >= self.destroy_votes else "destroy"


class EpilogueResult(BaseModel):
    kept: list[str] = Field(default_factory=list)  # card ids to keep
    destroyed: list[str] = Field(default_factory=list)  # card ids to destroy
    tallies: list[CardVotes] = Field(default_factory=list)


def tally_votes(
    votes: dict[str, dict[str, str]],
    card_ids: list[str],
) -> EpilogueResult:
    """Tally keep/destroy votes across players.

    Args:
        votes: {player_id: {card_id: "keep" | "destroy"}}. A player may omit a
            card (treated as abstain).
        card_ids: all cards eligible for voting.

    Returns:
        EpilogueResult with kept and destroyed card id lists (order follows
        card_ids).
    """
    tallies: dict[str, CardVotes] = {cid: CardVotes(card_id=cid) for cid in card_ids}

    for player_votes in votes.values():
        for card_id, vote in player_votes.items():
            if card_id not in tallies:
                continue  # ignore votes for unknown cards
            tally = tallies[card_id]
            if vote == "keep":
                tallies[card_id] = tally.model_copy(update={"keep_votes": tally.keep_votes + 1})
            elif vote == "destroy":
                tallies[card_id] = tally.model_copy(update={"destroy_votes": tally.destroy_votes + 1})
            # "abstain" or unknown = no change

    kept: list[str] = []
    destroyed: list[str] = []
    tally_list: list[CardVotes] = []
    for cid in card_ids:
        t = tallies[cid]
        tally_list.append(t)
        if t.verdict() == "keep":
            kept.append(cid)
        else:
            destroyed.append(cid)

    return EpilogueResult(kept=kept, destroyed=destroyed, tallies=tally_list)
