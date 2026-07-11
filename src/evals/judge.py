"""evals.judge — multi-dimensional LLM-as-judge for card-interpretation evals.

Given (card description, agent's generated effect summary, human_canonical expected),
produce a structured Verdict scoring each dimension independently. This Verdict is
eval-only and distinct from agent.schemas.Verdict.
"""

from __future__ import annotations

import json
from typing import Annotated

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field


class Verdict(BaseModel):
    """Multi-dimensional judge verdict for one card interpretation."""

    intent_match: Annotated[
        float, Field(ge=0.0, le=1.0, description="Does the generated effect capture the card's intent?")
    ]
    timing_correct: Annotated[
        float, Field(ge=0.0, le=1.0, description="Is the timing (immediate/persistent/triggered) correct?")
    ]
    target_placement_correct: Annotated[
        float, Field(ge=0.0, le=1.0, description="Is the target (self/player/all) and placement correct?")
    ]
    trigger_event_correct: Annotated[
        float,
        Field(
            ge=0.0,
            le=1.0,
            description="Is the trigger event correct? 1.0 if N/A (no trigger). 0.0 if wrong or missing.",
        ),
    ]
    magnitude_sign_correct: Annotated[
        float, Field(ge=0.0, le=1.0, description="Is the magnitude sign (positive/negative/neutral) correct?")
    ]
    overall: Annotated[float, Field(ge=0.0, le=1.0, description="Overall faithfulness (0=wrong, 1=perfect).")]
    reason: str = Field(description="1-2 sentences explaining the overall score.")


JUDGE_SYSTEM = """\
You are a strict evaluator for a card-game interpretation system called "1000 Blank White Cards".

Given:
- CARD DESCRIPTION: the original card text as written by a player.
- GENERATED EFFECT SUMMARY: the interpretation produced by an AI agent (JSON or text summary).
- EXPECTED CANONICAL: the correct interpretation produced by a human annotator (JSON).

Score each dimension independently from 0.0 to 1.0:
- intent_match: Does the generated effect do what the card says? Focus on semantics, not phrasing.
- timing_correct: Is the timing tag correct? (immediate=one-shot on play; persistent=stays in play; triggered=fires on an event).
- target_placement_correct: Is the target correct? (self=card player, player=chosen player, all=everyone).
- trigger_event_correct: Is the trigger event correct? If the card has NO trigger, this should be 1.0 (N/A).
- magnitude_sign_correct: Is the sign of the effect correct? (positive=gaining, negative=losing, neutral=no change).
- overall: Holistic judgment of interpretation faithfulness.

Be strict. "all players" interpreted as "self" scores 0 for target_placement_correct.
A persistent card interpreted as immediate scores 0 for timing_correct.
"""


class JudgeLLM:
    """Multi-dimensional LLM judge. Stateless; create once and reuse across eval items."""

    def __init__(self, model: str = "gpt-5.4-mini") -> None:
        self._llm = ChatOpenAI(model=model, temperature=0).with_structured_output(Verdict)

    def evaluate(self, *, card_description: str, generated_summary: str, human_canonical: dict) -> Verdict:
        """Call the LLM judge and return a structured Verdict.

        Raises ValueError if the LLM response is not a Verdict.
        """
        user_msg = (
            f"CARD DESCRIPTION:\n{card_description}\n\n"
            f"GENERATED EFFECT SUMMARY:\n{generated_summary}\n\n"
            f"EXPECTED CANONICAL:\n{json.dumps(human_canonical, indent=2)}"
        )
        response = self._llm.invoke([SystemMessage(content=JUDGE_SYSTEM), HumanMessage(content=user_msg)])
        if not isinstance(response, Verdict):
            raise ValueError(f"Judge returned unexpected type: {type(response)}")
        return response
