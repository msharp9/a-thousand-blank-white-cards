"""tbwc.agent.state — InterpretState TypedDict threaded through the LangGraph graph."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:
    # Type-checker only; at runtime the graph passes plain dicts.
    from tbwc.agent.schemas import Interpretation, SnippetEffect, Verdict  # noqa: F401


class CardDraft(TypedDict):
    """The minimal card data passed into the interpretation pipeline."""

    title: str
    description: str


class InterpretState(TypedDict, total=False):
    """Mutable state threaded through the LangGraph interpretation graph.

    Fields are optional (total=False) because nodes fill them in incrementally and
    LangGraph merges partial dicts on each node return.

    Fields:
        card_draft: Input — the card's title and description.
        retrieved: Exemplar dicts from RAG search (added by the retrieve node).
        search_notes: Free-text notes from the web-search node (or None if skipped).
        interpretation: Structured classification from the classify node.
        program: EffectProgram from the emit_ops node (mode="immediate" path).
        snippet: SnippetEffect from the gen_snippet node (mode="snippet" path).
        verdict: Verdict from the judge node.
        attempts: How many times the classify->emit/gen->judge loop has run.
    """

    card_draft: CardDraft
    retrieved: list[dict[str, Any]]
    search_notes: str | None
    interpretation: Any  # tbwc.agent.schemas.Interpretation at runtime
    program: Any  # tbwc EffectProgram at runtime (Any avoids import cycle)
    snippet: Any  # tbwc.agent.schemas.SnippetEffect at runtime
    verdict: Any  # tbwc.agent.schemas.Verdict at runtime
    attempts: int
