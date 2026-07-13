"""Private persisted state for an atomic card resolution paused for input."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from models.effects import ResolutionPlan
from models.game_state import GameState
from models.interactions import InteractionDescriptor, InteractionResponsePayload


class PendingResolution(BaseModel):
    schema_version: int = 1
    interaction_id: str
    card_id: str
    actor_id: str
    card: dict[str, Any]
    plan: ResolutionPlan
    cursor: int = Field(ge=0)
    working_state: GameState
    request: InteractionDescriptor
    result_key: str
    resolved_audience: list[str]
    deadline_at: datetime
    responses: dict[str, InteractionResponsePayload] = Field(default_factory=dict)
    interactions: dict[str, Any] = Field(default_factory=dict)
    interaction_refs: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str
    chosen_player_id: str | None = None
    chosen_card_id: str | None = None
    before_scores: dict[str, int]
    deck_count_before: int
