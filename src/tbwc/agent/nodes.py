"""tbwc.agent.nodes — LangGraph interpretation nodes.

Each node is a pure-ish function (InterpretState) -> dict (partial state update).
Later beads append more nodes (retrieve, classify, emit_ops, judge, …) to this file.
"""

from __future__ import annotations

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
