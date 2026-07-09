"""tbwc.agent.nodes — LangGraph interpretation nodes.

Each node is a pure-ish function (InterpretState) -> dict (partial state update).
Later beads append more nodes (retrieve, classify, emit_ops, judge, …) to this file.
"""

from __future__ import annotations

import re

from tbwc.agent.llm import get_chat_model
from tbwc.agent.prompts import INTERPRETER_SYSTEM
from tbwc.agent.state import InterpretState
from tbwc.rag.retrievers import dense_retriever


def reason(state: InterpretState) -> dict:
    """Summarise the card's intent in one sentence to guide RAG retrieval.

    Reads: state["card_draft"]
    Writes: state["search_notes"] (a one-sentence intent summary string)
    """
    draft = state["card_draft"]
    llm = get_chat_model(temperature=0)
    messages = [
        {"role": "system", "content": INTERPRETER_SYSTEM},
        {
            "role": "human",
            "content": (
                f"Card title: {draft['title']}\n"
                f"Card description: {draft['description']}\n\n"
                "In ONE sentence, summarise what this card is intended to do "
                "so we can search for similar example cards."
            ),
        },
    ]
    response = llm.invoke(messages)
    return {"search_notes": response.content}


# ---------------------------------------------------------------------------
# retrieve node
# ---------------------------------------------------------------------------

_retriever = dense_retriever()


def retrieve(state: InterpretState) -> dict:
    """Search the RAG store for exemplar cards similar to the card being interpreted.

    Reads: state["card_draft"], state["search_notes"]
    Writes: state["retrieved"] (list of exemplar dicts from the RAG search)

    Uses search_notes (intent summary) as the query if available, else falls back
    to "title\\ndescription".
    """
    draft = state["card_draft"]
    query = state.get("search_notes") or f"{draft['title']}\n{draft['description']}"
    exemplars = _retriever(query, k=4)
    return {"retrieved": exemplars}


# ---------------------------------------------------------------------------
# route_search node + edge function
# ---------------------------------------------------------------------------

# Heuristic: flag a card for web search if it contains a quoted phrase or a
# multi-word proper noun (consecutive capitalised words) — signals of external
# references the LLM may not know from the card text alone.
_SEARCH_TRIGGERS = re.compile(
    r'"[^"]+"'  # quoted phrase
    r"|'[^']+'"  # single-quoted phrase
    r"|\b(?:the\s+)?[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+"  # multi-word proper noun
)


def route_search(state: InterpretState) -> dict:
    """Heuristically decide whether a web search is needed.

    Reads: state["card_draft"], state["search_notes"]
    Writes: state["search_notes"] — appends " [web_search=yes]" or " [web_search=no]".

    Does NOT perform the search; only sets a flag the conditional edge reads.
    """
    draft = state["card_draft"]
    text = f"{draft['title']} {draft['description']}"
    needs_search = bool(_SEARCH_TRIGGERS.search(text))
    existing_notes = state.get("search_notes") or ""
    suffix = " [web_search=yes]" if needs_search else " [web_search=no]"
    return {"search_notes": existing_notes + suffix}


def should_search(state: InterpretState) -> str:
    """Conditional edge function: route to 'search' or 'classify'."""
    notes = state.get("search_notes", "")
    if "[web_search=yes]" in notes:
        return "search"
    return "classify"


# ---------------------------------------------------------------------------
# search node  (STUB — Tavily integration wired in a later phase)
# ---------------------------------------------------------------------------


def search(state: InterpretState) -> dict:
    """Perform a web search for additional context about the card.

    STUB: returns a no-op note so the graph proceeds to 'classify'. A later phase
    replaces this body with a Tavily API call.

    Reads: state["card_draft"], state["search_notes"]
    Writes: state["search_notes"] — appends a stub notice.
    """
    # TODO(phase4): call the Tavily search API here
    existing = state.get("search_notes") or ""
    stub_note = " [web_search_results: none — search stub not yet implemented]"
    return {"search_notes": existing + stub_note}
